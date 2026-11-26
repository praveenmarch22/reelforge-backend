// Tests for the render service (renderer/server.mjs; API design group K; milestone B5).
//
// They talk to a running service over HTTP, so the service is tested as the worker sees it.
// The service only downloads from and uploads to RENDER_ALLOWED_ORIGINS, and this test serves the
// upload links, so start a service that allows the test's address, then run the test beside it:
//
//   docker run -d --name rut --network <project>_default --shm-size 1g \
//     -e RENDER_SERVICE_TOKEN=<16+ characters> -e RENDER_ALLOWED_ORIGINS=http://rtest:4100 \
//     -e RENDER_SCALE=0.5 <project>-renderer
//   docker run --rm --network <project>_default --network-alias rtest -v <path to renderer>:/r \
//     -e RENDER_URL=http://rut:3000 -e RENDER_TOKEN=<the token> -e RENDER_SCALE=0.5 \
//     -e UPLOAD_BASE=http://rtest:4100 <project>-renderer node --test /r/test/server.test.mjs
//
// The start-up checks run /app/server.mjs of that image (SERVER_PATH) with missing settings.
//
// Renders here are tiny (108x192, colour scenes, no media) so they finish in seconds; the
// "long" one is full size and is always cancelled.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {after, before, test} from 'node:test';

const RENDER_URL = process.env.RENDER_URL ?? 'http://localhost:3100';
const TOKEN = process.env.RENDER_TOKEN ?? 'dev-only-render-token';
const UPLOAD_BASE = process.env.UPLOAD_BASE ?? 'http://host.docker.internal:4100';
const UPLOAD_PORT = Number(new URL(UPLOAD_BASE).port || 80);
const pkg = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
const AUTH = {Authorization: `Bearer ${TOKEN}`};
const SCALE = Number(process.env.RENDER_SCALE ?? 1);
const SERVER_PATH = process.env.SERVER_PATH ?? '/app/server.mjs';

// ------------------------------------------------------------------ the upload target

/** @type {Map<string, {contentType: string, body: Buffer}>} */
const uploads = new Map();
const uploadServer = http.createServer((req, res) => {
  const chunks = [];
  req.on('data', (c) => chunks.push(c));
  req.on('end', () => {
    if (req.method === 'PUT' && req.url.startsWith('/upload/')) {
      uploads.set(req.url, {contentType: req.headers['content-type'], body: Buffer.concat(chunks)});
      res.writeHead(200).end();
    } else {
      res.writeHead(404).end('missing');
    }
  });
});

before(async () => {
  await new Promise((resolve) => uploadServer.listen(UPLOAD_PORT, '0.0.0.0', resolve));
  await until(async () => (await fetch(`${RENDER_URL}/health`)).status === 200, 300_000, 'the service to start');
});
after(() => uploadServer.close());

// ------------------------------------------------------------------ helpers

let counter = 0;
const newId = (name) => `t-${process.pid}-${Date.now()}-${++counter}-${name}`;

function timeline({frames = 12, width = 108, height = 192, src = ''} = {}) {
  return {
    version: 1, projectId: 'test', fps: 30, width, height, durationInFrames: frames,
    captionStyle: 'clean', showCaptions: false, voiceVolume: 1, transition: 'cut', music: null,
    scenes: [{
      id: 's1', startFrame: 0, durationInFrames: frames, mood: 'calm', onScreenText: 'Test',
      visual: {kind: 'image', src, durationSeconds: null, motion: 'zoom-in'},
      voice: {src: '', durationSeconds: null}, words: [],
    }],
  };
}

function job(id, options) {
  return {
    renderId: id,
    timeline: timeline(options),
    videoUploadUrl: `${UPLOAD_BASE}/upload/${id}.mp4`,
    thumbnailUploadUrl: `${UPLOAD_BASE}/upload/${id}.jpeg`,
  };
}

async function call(method, path, {body, headers = AUTH, raw} = {}) {
  const res = await fetch(RENDER_URL + path, {
    method,
    headers: {...headers, ...(body !== undefined || raw !== undefined ? {'Content-Type': 'application/json'} : {})},
    body: raw ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* not JSON */ }
  return {status: res.status, json};
}

const start = (body) => call('POST', '/renders', {body});
const status = async (id) => (await call('GET', `/renders/${id}`)).json;

async function until(check, ms, what) {
  const end = Date.now() + ms;
  for (;;) {
    try { if (await check()) return; } catch { /* not yet */ }
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 200));
  }
}

const finished = (id, ms = 180_000) =>
  until(async () => ['succeeded', 'failed', 'cancelled'].includes((await status(id)).state), ms, `${id} to end`);

const uploadedFor = (id) => [...uploads.keys()].filter((k) => k.includes(id));

// ------------------------------------------------------------------ K3 health

test('health needs no token and reports the Remotion version and an idle queue', async () => {
  const res = await call('GET', '/health', {headers: {}});
  assert.equal(res.status, 200);
  assert.equal(res.json.status, 'ok');
  assert.equal(typeof res.json.busy, 'boolean');
  assert.equal(typeof res.json.waiting, 'number');
  assert.equal(res.json.remotionVersion, pkg.dependencies.remotion);
});

// ------------------------------------------------------------------ auth

test('every other request needs the Bearer token', async () => {
  for (const headers of [{}, {Authorization: 'Bearer wrong'}, {Authorization: TOKEN}, {Authorization: `bearer ${TOKEN}`}]) {
    assert.equal((await call('POST', '/renders', {headers, body: job(newId('auth'))})).status, 401);
    assert.equal((await call('GET', '/renders/abc', {headers})).status, 401);
    assert.equal((await call('DELETE', '/renders/abc', {headers})).status, 401);
  }
  assert.deepEqual(uploadedFor('auth'), []);
});

// ------------------------------------------------------------------ K1 bad bodies

test('a body that is not a render request is refused with 400', async () => {
  const good = job(newId('bad'));
  const bodies = [
    {raw: '{not json'},
    {raw: ''},
    {body: null},
    {body: []},
    {body: {...good, renderId: undefined}},
    {body: {...good, renderId: ''}},
    {body: {...good, renderId: 'has/slash'}},
    {body: {...good, renderId: 'x'.repeat(65)}},
    {body: {...good, timeline: undefined}},
    {body: {...good, timeline: {fps: 30}}},
    {body: {...good, videoUploadUrl: undefined}},
    {body: {...good, thumbnailUploadUrl: ''}},
  ];
  for (const b of bodies) {
    const res = await call('POST', '/renders', b);
    assert.equal(res.status, 400, `expected 400 for ${JSON.stringify(b).slice(0, 80)}, got ${res.status}`);
    assert.ok(res.json?.error);
  }
});

test('a renderId that is not a string is refused, since it could never be polled or stopped', async () => {
  const id = Math.floor(Math.random() * 1e9);
  const res = await start({...job(String(id)), renderId: id});
  if (res.status === 202) {
    // Accepted: it is rendering now, under an id that GET and DELETE can't find.
    const polled = (await call('GET', `/renders/${id}`)).status;
    const stopped = (await call('DELETE', `/renders/${id}`)).status;
    await until(async () => (await call('GET', '/health', {headers: {}})).json.busy === false, 120_000, 'the stray render');
    assert.fail(`a numeric renderId was accepted (202); GET answered ${polled} and DELETE ${stopped}`);
  }
  assert.equal(res.status, 400);
});

test('a body over 5 MB is refused and the service keeps answering', async () => {
  const huge = JSON.stringify({...job(newId('huge')), padding: 'x'.repeat(6 * 1024 * 1024)});
  let code = null;
  try {
    code = (await call('POST', '/renders', {raw: huge})).status;
  } catch {
    code = 'connection closed';
  }
  assert.ok(code === 400 || code === 413 || code === 'connection closed', `got ${code}`);
  assert.equal((await call('GET', '/health', {headers: {}})).status, 200);
});

test('unknown renders and addresses answer 404', async () => {
  assert.equal((await call('GET', '/renders/never-started')).status, 404);
  assert.equal((await call('DELETE', '/renders/never-started')).status, 404);
  assert.equal((await call('GET', '/nothing-here')).status, 404);
  assert.equal((await call('GET', `/renders/${'x'.repeat(65)}`)).status, 404);
});

test('links that do not point at an allowed origin are refused with 400 and nothing runs', async () => {
  const outside = ['http://evil.example/x.png', 'http://rtest:4101/x.png', 'https://rtest:4100/x.png',
    `http://rtest:4100@evil.example/x.png`, 'file:///etc/passwd', 'not a url'];
  for (const link of outside) {
    const id = newId('origin');
    const base = job(id);
    const variants = [
      {...base, videoUploadUrl: link},
      {...base, thumbnailUploadUrl: link},
      {...base, timeline: timeline({src: link})},
      {...base, timeline: {...timeline(), scenes: [{...timeline().scenes[0], voice: {src: link, durationSeconds: 1}}]}},
      {...base, timeline: {...timeline(), music: {src: link, volume: 0.2, duckedVolume: 0.1, fadeInSeconds: 0, fadeOutSeconds: 0}}},
    ];
    for (const body of variants) {
      const res = await start(body);
      assert.equal(res.status, 400, `${link} was accepted`);
    }
    assert.equal((await call('GET', `/renders/${id}`)).status, 404);
  }
});

// ------------------------------------------------------------------ start-up checks

function exitOf(env) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [SERVER_PATH], {env: {PATH: process.env.PATH, PORT: '3999', ...env}, stdio: 'pipe'});
    let err = '';
    child.stderr.on('data', (d) => { err += d; });
    const timer = setTimeout(() => { child.kill(); resolve({code: 'still running', err}); }, 20_000);
    child.on('exit', (code) => { clearTimeout(timer); resolve({code, err}); });
  });
}

test('the service refuses to start without a token, allowed origins or a sane scale', {skip: !fs.existsSync(SERVER_PATH) && `${SERVER_PATH} not found`}, async () => {
  const good = {RENDER_SERVICE_TOKEN: 'x'.repeat(16), RENDER_ALLOWED_ORIGINS: 'http://minio:9000'};
  const cases = [
    [{...good, RENDER_SERVICE_TOKEN: undefined}, /RENDER_SERVICE_TOKEN/],
    [{...good, RENDER_SERVICE_TOKEN: 'x'.repeat(15)}, /RENDER_SERVICE_TOKEN/],
    [{...good, RENDER_ALLOWED_ORIGINS: undefined}, /RENDER_ALLOWED_ORIGINS/],
    [{...good, RENDER_ALLOWED_ORIGINS: ' , '}, /RENDER_ALLOWED_ORIGINS/],
    [{...good, RENDER_SCALE: '0'}, /RENDER_SCALE/],
    [{...good, RENDER_SCALE: '1.5'}, /RENDER_SCALE/],
    [{...good, RENDER_SCALE: 'big'}, /RENDER_SCALE/],
  ];
  for (const [env, message] of cases) {
    const clean = Object.fromEntries(Object.entries(env).filter(([, v]) => v !== undefined));
    const {code, err} = await exitOf(clean);
    assert.notEqual(code, 0, `started with ${JSON.stringify(clean)}`);
    assert.notEqual(code, 'still running', `kept running with ${JSON.stringify(clean)}`);
    assert.match(err, message);
  }
});

// ------------------------------------------------------------------ a real render

test('a tiny render makes an MP4 and a JPEG cover and uploads both', async () => {
  const id = newId('tiny');
  const first = await start(job(id, {frames: 12}));
  assert.equal(first.status, 202);
  assert.ok(['queued', 'running'].includes(first.json.state));
  assert.equal(first.json.progress, 0);
  await finished(id);
  const done = await status(id);
  assert.equal(done.state, 'succeeded', done.error);
  assert.equal(done.progress, 100);
  assert.equal(done.durationSeconds, 0.4);
  const video = uploads.get(`/upload/${id}.mp4`);
  const cover = uploads.get(`/upload/${id}.jpeg`);
  assert.ok(video && cover, 'both files uploaded');
  assert.equal(video.contentType, 'video/mp4');
  assert.equal(cover.contentType, 'image/jpeg');
  assert.equal(video.body.subarray(4, 8).toString('latin1'), 'ftyp');
  assert.deepEqual([...cover.body.subarray(0, 2)], [0xff, 0xd8]);
  assert.equal(done.sizeBytes, video.body.length);
  // RENDER_SCALE: the size it rendered at, even numbers for H.264.
  assert.equal(done.width, Math.round((108 * SCALE) / 2) * 2);
  assert.equal(done.height, Math.round((192 * SCALE) / 2) * 2);

  // The same render asked for again answers with its status instead of rendering again.
  uploads.delete(`/upload/${id}.mp4`);
  const again = await start(job(id, {frames: 12}));
  assert.equal(again.status, 202);
  assert.equal(again.json.state, 'succeeded');
  assert.equal(uploads.has(`/upload/${id}.mp4`), false);
});

test('a file that cannot be downloaded fails the render and nothing is uploaded', async () => {
  const id = newId('broken');
  assert.equal((await start(job(id, {frames: 12, src: `${UPLOAD_BASE}/missing.png`}))).status, 202);
  await finished(id);
  const done = await status(id);
  assert.equal(done.state, 'failed');
  assert.ok(typeof done.error === 'string' && done.error.length > 0);
  assert.ok(done.error.length <= 500);
  assert.deepEqual(uploadedFor(id), []);
});

// ------------------------------------------------------------------ the queue and K2b

test('renders run one at a time in order, and DELETE drops a waiting one and stops a running one', async () => {
  const long = newId('long');
  const dropped = newId('dropped');
  const next1 = newId('next1');
  const next2 = newId('next2');
  assert.equal((await start(job(long, {frames: 300, width: 1080, height: 1920}))).status, 202);
  await until(async () => (await status(long)).state === 'running', 30_000, 'the long render to run');
  for (const id of [dropped, next1, next2]) assert.equal((await start(job(id))).status, 202);

  const health = (await call('GET', '/health', {headers: {}})).json;
  assert.equal(health.busy, true);
  assert.equal(health.waiting, 3);
  for (const id of [dropped, next1, next2]) assert.equal((await status(id)).state, 'queued');

  // A waiting render is dropped.
  const del = await call('DELETE', `/renders/${dropped}`);
  assert.equal(del.status, 200);
  assert.equal(del.json.state, 'cancelled');
  assert.equal((await call('GET', '/health', {headers: {}})).json.waiting, 2);

  // The running one is stopped; the next ones then run in the order they came.
  const seen = [];
  const watcher = setInterval(async () => {
    try { seen.push([(await status(next1)).state, (await status(next2)).state]); } catch { /* ignore */ }
  }, 100);
  const stop = await call('DELETE', `/renders/${long}`);
  assert.equal(stop.status, 200);
  // "cancelling" until drawing has really stopped (QB5-015), then "cancelled".
  assert.equal(stop.json.state, 'cancelling');
  await finished(long);
  await finished(next2);
  clearInterval(watcher);

  assert.equal((await status(long)).state, 'cancelled');
  assert.equal((await status(dropped)).state, 'cancelled');
  assert.equal((await status(next1)).state, 'succeeded');
  assert.equal((await status(next2)).state, 'succeeded');
  assert.ok((await status(next1)).finishedAt <= (await status(next2)).finishedAt);
  assert.ok(!seen.some(([a, b]) => b === 'running' && a !== 'succeeded'), 'the second ran before the first ended');
  assert.deepEqual(uploadedFor(long), [], 'a stopped render uploads nothing');
  assert.deepEqual(uploadedFor(dropped), [], 'a dropped render never runs');

  const idle = (await call('GET', '/health', {headers: {}})).json;
  assert.equal(idle.busy, false);
  assert.equal(idle.waiting, 0);
});

test('DELETE of a finished render changes nothing', async () => {
  const id = newId('finished');
  await start(job(id));
  await finished(id);
  const res = await call('DELETE', `/renders/${id}`);
  assert.equal(res.status, 200);
  assert.equal(res.json.state, 'succeeded');
  assert.equal((await status(id)).state, 'succeeded');
});
