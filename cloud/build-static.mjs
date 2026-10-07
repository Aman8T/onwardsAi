#!/usr/bin/env node
/**
 * Prepares cloud/ for `fastapi deploy`, which uploads only this folder.
 *
 *   1. cloud/static/         <- dist/ (the Vite build; run `vite build` first)
 *   2. cloud/tayyari/sample/ <- the sample persona fixtures (*.md, *.json) from server/sample/
 *
 * Run it through `npm run cloud:build` so the Vite build always precedes the copy.
 * It only copies files: it never writes Python source, and it only deletes cloud/static.
 */
import { copyFile, mkdir, readdir, rm, stat } from 'node:fs/promises';
import { dirname, extname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const CLOUD_DIR = dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = resolve(CLOUD_DIR, '..');

const DIST_DIR = join(ROOT_DIR, 'dist');
const STATIC_DIR = join(CLOUD_DIR, 'static');
const SAMPLE_SRC_DIR = join(ROOT_DIR, 'server', 'sample');
const SAMPLE_DST_DIR = join(CLOUD_DIR, 'tayyari', 'sample');

/** Fixture types in server/sample/; index.ts is the Node-side loader and has no use in Python. */
const FIXTURE_EXTENSIONS = new Set(['.md', '.json']);
/** The loader in cloud/tayyari/sample/sample.py cannot work without these. */
const REQUIRED_FIXTURES = ['sample-profile.json', 'sample-context.json'];

const show = (path) => relative(ROOT_DIR, path).split('\\').join('/');
const kb = (bytes) => `${(bytes / 1024).toFixed(1)} KB`;

function fail(message) {
  console.error(`cloud:build failed: ${message}`);
  process.exit(1);
}

async function isFile(path) {
  try {
    return (await stat(path)).isFile();
  } catch {
    return false;
  }
}

/** Recursively copies `src` into `dst`, returning one { path, bytes } record per file. */
async function copyTree(src, dst) {
  await mkdir(dst, { recursive: true });
  const copied = [];
  for (const entry of await readdir(src, { withFileTypes: true })) {
    const from = join(src, entry.name);
    const to = join(dst, entry.name);
    if (entry.isDirectory()) {
      copied.push(...(await copyTree(from, to)));
    } else if (entry.isFile()) {
      await copyFile(from, to);
      copied.push({ path: to, bytes: (await stat(to)).size });
    }
  }
  return copied;
}

async function copyStatic() {
  if (!(await isFile(join(DIST_DIR, 'index.html')))) {
    fail(`${show(DIST_DIR)}/index.html not found; run \`npm run cloud:build\` (it runs vite build first).`);
  }
  await rm(STATIC_DIR, { recursive: true, force: true });
  const files = await copyTree(DIST_DIR, STATIC_DIR);
  const total = files.reduce((sum, file) => sum + file.bytes, 0);
  console.log(`static: ${show(DIST_DIR)}/ -> ${show(STATIC_DIR)}/ (${files.length} files, ${kb(total)})`);
  for (const file of files) console.log(`  ${show(file.path)}  ${kb(file.bytes)}`);
}

/** Lists the fixtures to copy; fails before anything is touched when the sources are incomplete. */
async function findFixtures() {
  let names;
  try {
    names = await readdir(SAMPLE_SRC_DIR);
  } catch {
    fail(`${show(SAMPLE_SRC_DIR)} not found.`);
  }
  const fixtures = names.filter((name) => FIXTURE_EXTENSIONS.has(extname(name))).sort();
  const missing = REQUIRED_FIXTURES.filter((name) => !fixtures.includes(name));
  if (missing.length > 0) fail(`missing in ${show(SAMPLE_SRC_DIR)}: ${missing.join(', ')}`);
  return { fixtures, skipped: names.filter((name) => !fixtures.includes(name)) };
}

async function copySample({ fixtures, skipped }) {
  await mkdir(SAMPLE_DST_DIR, { recursive: true });
  console.log(`sample: ${show(SAMPLE_SRC_DIR)}/ -> ${show(SAMPLE_DST_DIR)}/ (${fixtures.length} fixtures)`);
  for (const name of fixtures) {
    const to = join(SAMPLE_DST_DIR, name);
    await copyFile(join(SAMPLE_SRC_DIR, name), to);
    console.log(`  ${show(to)}  ${kb((await stat(to)).size)}`);
  }
  if (skipped.length > 0) console.log(`  skipped (not fixtures): ${skipped.join(', ')}`);
}

const sample = await findFixtures();
await copyStatic();
await copySample(sample);
console.log('Done. Deploy from cloud/ (see cloud/DEPLOY.md).');
