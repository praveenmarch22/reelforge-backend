// The render service (technical design, "Render service"; milestone B5).
//
//   POST /renders      {renderId, timeline, videoUploadUrl, thumbnailUploadUrl}  -> 202 {state}
//   GET  /renders/:id  -> {state, progress, error, sizeBytes, durationSeconds}
//   DELETE /renders/:id -> stops it (queued: dropped; running: Remotion is cancelled, nothing uploaded)
//   GET  /health       -> {status: "ok", busy, waiting, remotionVersion}
//
// Every request but /health carries `Authorization: Bearer <RENDER_SERVICE_TOKEN>` when the
// service is given a token (always, outside a developer's machine).
//
// The timeline's files are links the service can download; the finished MP4 and its cover image
// are uploaded to the upload links made for each render, so it never holds storage credentials.
// The Remotion bundle is built once at start. One render runs at a time; the rest wait in order.
// The worker starts a render and polls its progress; this service keeps no other state.

import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {timingSafeEqual} from 'node:crypto';
import {bundle} from '@remotion/bundler';
import {makeCancelSignal, renderMedia, renderStill, selectComposition} from '@remotion/renderer';
import {VERSION} from 'remotion/version';

const root = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT ?? 3000);
const TOKEN = process.env.RENDER_SERVICE_TOKEN ?? '';
if (TOKEN.length < 16) throw new Error('RENDER_SERVICE_TOKEN must be set (16 characters at least)');
// The only places the service downloads from or uploads to: storage's address (QB5-008).
const ALLOWED = (process.env.RENDER_ALLOWED_ORIGINS ?? '')
  .split(',')
  .map((o) => o.trim())
  .filter(Boolean)
  .map((o) => new URL(o).origin); // "http://Minio:9000/" and "http://minio:9000" are the same
if (!ALLOWED.length) throw new Error('RENDER_ALLOWED_ORIGINS must list the storage origin');
// How many browser tabs render frames side by side; empty = Remotion's default (half the CPUs).
// Output size relative to the timeline's 1080x1920. A developer's machine renders at 2/3
// (720x1280, about twice as fast); production leaves it at 1 (product decision, B5).
// Written as a fraction ("2/3") so 1080x1920 scales to whole pixels: Remotion refuses anything
// else, and 0.6667 gives 1280.064.
const SCALE = (() => {
  const text = String(process.env.RENDER_SCALE ?? '1').trim();
  if (!/^\d+(\.\d+)?(\/\d+(\.\d+)?)?$/.test(text)) throw new Error('RENDER_SCALE must be a number or a fraction such as 2/3');
  const [top, bottom = '1'] = text.split('/');
  return Number(top) / Number(bottom);
})();
if (!(SCALE > 0 && SCALE <= 1)) throw new Error('RENDER_SCALE must be above 0 and at most 1, for example 2/3');
// Each side must come out whole and even: Remotion refuses fractions, and H.264 needs even sides.
for (const side of [1080, 1920]) {
  const scaled = side * SCALE;
  if (Math.abs(scaled - Math.round(scaled)) > 1e-9 || Math.round(scaled) % 2 !== 0) {
    throw new Error(`RENDER_SCALE must turn ${side} pixels into a whole, even number`);
  }
}
const CONCURRENCY = process.env.RENDER_CONCURRENCY ? Number(process.env.RENDER_CONCURRENCY) : null;
const MAX_BODY = 5 * 1024 * 1024; // a 90 s timeline is far smaller
const KEEP_FINISHED_MS = 60 * 60 * 1000; // a finished render's status is kept for an hour
const COVER_SECONDS = 1; // the cover image is the frame one second in (or the middle of a short Reel)

const log = (message, extra = {}) =>
  process.stdout.write(JSON.stringify({time: new Date().toISOString(), message, ...extra}) + '\n');

/** @type {Map<string, {state: string, progress: number, error: string | null, sizeBytes?: number, durationSeconds?: number, finishedAt?: number, cancel?: () => void}>} */
const renders = new Map();
const waiting = [];
let busy = false;
let serveUrl = null;

async function upload(url, file, contentType) {
  const size = fs.statSync(file).size;
  const response = await fetch(url, {
    method: 'PUT',
    headers: {'Content-Type': contentType, 'Content-Length': String(size)},
    body: fs.createReadStream(file),
    duplex: 'half',
  });
  if (!response.ok) throw new Error(`upload failed with ${response.status}`);
  return size;
}

async function run({renderId, timeline, videoUploadUrl, thumbnailUploadUrl}) {
  const status = renders.get(renderId);
  status.state = 'running';
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'render-'));
  const video = path.join(dir, 'reel.mp4');
  const cover = path.join(dir, 'cover.jpeg');
  const started = Date.now();
  const {cancelSignal, cancel} = makeCancelSignal();
  status.cancel = cancel;
  try {
    const composition = await selectComposition({serveUrl, id: 'Reel', inputProps: timeline, chromiumOptions: {gl: 'angle'}});
    await renderMedia({
      composition,
      serveUrl,
      inputProps: timeline,
      codec: 'h264',
      audioCodec: 'aac',
      audioBitrate: '128k', // Meta's Reels specification (QB5 Q3)
      crf: 20,
      // R-5: well under Instagram's size limit even for 90 s (about 90 MB at most).
      videoBitrate: null,
      encodingMaxRate: '8M',
      encodingBufferSize: '16M',
      outputLocation: video,
      cancelSignal,
      concurrency: CONCURRENCY,
      scale: SCALE,
      // Measured on the development machine (B5): ANGLE draws faster than SwiftShader, the
      // "veryfast" encoder preset halves encoding time for a slightly larger file, and more
      // decoder threads help pull frames out of the stock clips.
      chromiumOptions: {gl: 'angle'},
      x264Preset: 'veryfast',
      offthreadVideoThreads: 4,
      // A file that can't be downloaded or decoded fails the render with Remotion's own message.
      onProgress: ({progress}) => {
        status.progress = Math.min(95, Math.round(progress * 95));
      },
    });
    const frame = Math.min(Math.round(COVER_SECONDS * composition.fps), Math.floor(composition.durationInFrames / 2));
    await renderStill({composition, serveUrl, inputProps: timeline, frame, output: cover, imageFormat: 'jpeg', jpegQuality: 85, scale: SCALE, cancelSignal, chromiumOptions: {gl: 'angle'}});
    if (status.state === 'cancelling') throw new Error('cancelled');
    status.sizeBytes = await upload(videoUploadUrl, video, 'video/mp4');
    await upload(thumbnailUploadUrl, cover, 'image/jpeg');
    if (status.state === 'cancelling') throw new Error('cancelled'); // the worker deletes what was uploaded
    status.durationSeconds = Math.round((composition.durationInFrames / composition.fps) * 100) / 100;
    // Even sizes: H.264 needs them, and Remotion rounds the same way.
    status.width = Math.round(composition.width * SCALE);
    status.height = Math.round(composition.height * SCALE);
    status.state = 'succeeded';
    status.progress = 100;
    log('render succeeded', {renderId, seconds: (Date.now() - started) / 1000, sizeBytes: status.sizeBytes});
  } catch (error) {
    if (status.state === 'cancelling') {
      status.state = 'cancelled';
      log('render cancelled', {renderId});
    } else {
      status.state = 'failed';
      status.error = String(error?.message ?? error).slice(0, 500);
      log('render failed', {renderId, error: status.error});
    }
  } finally {
    if (status.state === 'cancelling') status.state = 'cancelled'; // it ended anyway
    delete status.cancel;
    status.finishedAt = Date.now();
    fs.rmSync(dir, {recursive: true, force: true});
  }
}

async function next() {
  if (busy || !waiting.length) return;
  busy = true;
  try {
    await run(waiting.shift());
  } finally {
    busy = false;
    void next();
  }
}

function forget() {
  const now = Date.now();
  for (const [id, status] of renders) {
    if (status.finishedAt && now - status.finishedAt > KEEP_FINISHED_MS) renders.delete(id);
  }
}

const send = (res, code, body) => {
  res.writeHead(code, {'Content-Type': 'application/json'});
  res.end(JSON.stringify(body, (key, value) => (key === 'cancel' ? undefined : value)));
};

function authorised(header) {
  const given = Buffer.from(String(header ?? ''));
  const wanted = Buffer.from(`Bearer ${TOKEN}`);
  return given.length === wanted.length && timingSafeEqual(given, wanted);
}

const allowedUrl = (value) => {
  try {
    return typeof value === 'string' && ALLOWED.includes(new URL(value).origin);
  } catch {
    return false;
  }
};

/** Every link in the timeline and both upload links must point at storage. */
function linksAllowed(timeline, videoUploadUrl, thumbnailUploadUrl) {
  if (!allowedUrl(videoUploadUrl) || !allowedUrl(thumbnailUploadUrl)) return false;
  const files = [];
  for (const scene of timeline.scenes) files.push(scene?.visual?.src, scene?.voice?.src);
  if (timeline.music) files.push(timeline.music.src);
  return files.every((link) => !link || allowedUrl(link)); // an empty link draws the placeholder
}

function cancelRender(id) {
  const status = renders.get(id);
  if (!status) return null;
  if (status.state === 'queued') {
    const index = waiting.findIndex((w) => w.renderId === id);
    if (index >= 0) waiting.splice(index, 1);
    status.state = 'cancelled';
    status.finishedAt = Date.now();
  } else if (status.state === 'running') {
    // "cancelling" until drawing and any upload under way have stopped, so the worker can wait
    // for that before it deletes the files (QB5-015).
    status.state = 'cancelling';
    status.cancel?.();
  }
  return status;
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY) {
        reject(new Error('too large'));
        req.destroy();
      } else chunks.push(chunk);
    });
    req.on('end', () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      } catch {
        reject(new Error('not JSON'));
      }
    });
    req.on('error', reject);
  });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', 'http://renderer');
  if (req.method === 'GET' && url.pathname === '/health') {
    return send(res, serveUrl ? 200 : 503, {
      status: serveUrl ? 'ok' : 'starting',
      busy,
      waiting: waiting.length,
      remotionVersion: VERSION,
    });
  }
  if (!authorised(req.headers.authorization)) return send(res, 401, {error: 'unauthorised'});
  const match = url.pathname.match(/^\/renders\/([A-Za-z0-9-]{1,64})$/);
  if (req.method === 'GET' && match) {
    const status = renders.get(match[1]);
    return status ? send(res, 200, status) : send(res, 404, {error: 'unknown render'});
  }
  if (req.method === 'DELETE' && match) {
    const status = cancelRender(match[1]);
    return status ? send(res, 200, status) : send(res, 404, {error: 'unknown render'});
  }
  if (req.method === 'POST' && url.pathname === '/renders') {
    if (!serveUrl) return send(res, 503, {error: 'starting'});
    let body;
    try {
      body = await readJson(req);
    } catch (error) {
      return send(res, 400, {error: String(error.message)});
    }
    const {renderId, timeline, videoUploadUrl, thumbnailUploadUrl} = body ?? {};
    if (typeof renderId !== 'string' || !/^[A-Za-z0-9-]{1,64}$/.test(renderId) || !timeline?.scenes || !videoUploadUrl || !thumbnailUploadUrl) {
      return send(res, 400, {error: 'renderId, timeline, videoUploadUrl and thumbnailUploadUrl are required'});
    }
    if (!Array.isArray(timeline.scenes) || !linksAllowed(timeline, videoUploadUrl, thumbnailUploadUrl)) {
      return send(res, 400, {error: 'links must point at storage'});
    }
    if (renders.has(renderId)) return send(res, 202, renders.get(renderId)); // the same render asked again
    forget();
    renders.set(renderId, {state: 'queued', progress: 0, error: null});
    waiting.push({renderId, timeline, videoUploadUrl, thumbnailUploadUrl});
    void next();
    return send(res, 202, renders.get(renderId));
  }
  return send(res, 404, {error: 'not found'});
});

log('bundling');
serveUrl = await bundle({entryPoint: path.join(root, 'src', 'index.ts')});
server.listen(PORT, () => log('render service ready', {port: PORT}));
