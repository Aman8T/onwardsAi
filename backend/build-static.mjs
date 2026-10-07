#!/usr/bin/env node
/**
 * Prepares backend/ for deployment by copying the built frontend.
 *
 *   1. backend/static/ <- dist/ (the Vite build; run `npm run build` or `npm run vite build` first)
 *
 * Sample fixtures already live in backend/tayyari/sample/ and are not copied here.
 *
 * Run it through `npm run build:static` after the Vite build completes.
 */
import { copyFile, mkdir, readdir, rm, stat } from 'node:fs/promises';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const BACKEND_DIR = dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = resolve(BACKEND_DIR, '..');

const DIST_DIR = join(ROOT_DIR, 'dist');
const STATIC_DIR = join(BACKEND_DIR, 'static');

const show = (path) => relative(ROOT_DIR, path).split('\\').join('/');
const kb = (bytes) => `${(bytes / 1024).toFixed(1)} KB`;

function fail(message) {
  console.error(`build:static failed: ${message}`);
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
    fail(`${show(DIST_DIR)}/index.html not found; run \`npm run build\` (it runs vite build first).`);
  }
  await rm(STATIC_DIR, { recursive: true, force: true });
  const files = await copyTree(DIST_DIR, STATIC_DIR);
  const total = files.reduce((sum, file) => sum + file.bytes, 0);
  console.log(`static: ${show(DIST_DIR)}/ -> ${show(STATIC_DIR)}/ (${files.length} files, ${kb(total)})`);
  for (const file of files) console.log(`  ${show(file.path)}  ${kb(file.bytes)}`);
}

await copyStatic();
console.log('Done. Deploy from backend/ (see backend/DEPLOY.md).');
