// Renders public/projects/<id>/timeline.json to out/<id>.mp4.
// Usage: node scripts/render.mjs <project-id>
// In the product this becomes the render service: same code, called with a project id or a timeline URL.

import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition} from '@remotion/renderer';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const projectId = process.argv[2];
if (!projectId) {
  console.error('Usage: node scripts/render.mjs <project-id>');
  process.exit(1);
}

const timelinePath = path.join(root, 'public', 'projects', projectId, 'timeline.json');
if (!fs.existsSync(timelinePath)) {
  console.error(`No timeline at ${timelinePath}. Run the pipeline build first.`);
  process.exit(1);
}
const inputProps = JSON.parse(fs.readFileSync(timelinePath, 'utf8').replace(/^﻿/, ''));

console.log('Bundling...');
const serveUrl = await bundle({entryPoint: path.join(root, 'src', 'index.ts')});
const composition = await selectComposition({serveUrl, id: 'Reel', inputProps});

const outDir = path.join(root, 'out');
fs.mkdirSync(outDir, {recursive: true});
const outputLocation = path.join(outDir, `${projectId}.mp4`);

let lastShown = -1;
const started = Date.now();
await renderMedia({
  composition,
  serveUrl,
  inputProps,
  codec: 'h264',
  audioCodec: 'aac',
  crf: 20,
  outputLocation,
  onProgress: ({progress}) => {
    const pct = Math.floor(progress * 100);
    if (pct !== lastShown && pct % 5 === 0) {
      lastShown = pct;
      process.stdout.write(`\rRendering ${pct}%`);
    }
  },
});

const seconds = ((Date.now() - started) / 1000).toFixed(1);
const sizeMb = (fs.statSync(outputLocation).size / 1e6).toFixed(1);
console.log(`\nDone in ${seconds}s: ${outputLocation} (${sizeMb} MB)`);
