#!/usr/bin/env node
/**
 * Copy opus-recorder's prebuilt worker into /public/audio so the browser
 * can fetch it as a static asset. opus-recorder ships the worker as a
 * .min.js file at dist/encoderWorker.min.js — we serve it from
 * /audio/encoderWorker.min.js at runtime.
 *
 * Runs as `postinstall` after every `npm install` so a fresh checkout
 * always has the worker available.
 */

import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const root = resolve(__dirname, "..");

const targets = [
  {
    from: resolve(root, "node_modules/opus-recorder/dist/encoderWorker.min.js"),
    to: resolve(root, "public/audio/encoderWorker.min.js"),
    label: "opus-recorder encoder worker",
    required: true,
  },
];

let missing = 0;
for (const t of targets) {
  if (!existsSync(t.from)) {
    if (t.required) {
      console.error(`[copy-audio-workers] MISSING: ${t.label} at ${t.from}`);
      console.error("  npm install probably failed for opus-recorder — try `npm install` again.");
      missing++;
    }
    continue;
  }
  mkdirSync(dirname(t.to), { recursive: true });
  copyFileSync(t.from, t.to);
  console.log(`[copy-audio-workers] copied ${t.label} -> ${t.to}`);
}

if (missing > 0) {
  process.exit(1);
}
