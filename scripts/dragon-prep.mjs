#!/usr/bin/env node
/**
 * Inspects a dragon model so its species file can be written: lists the animation clips, the bones
 * that look like a head or jaw, the textures and their sizes, and warns about things the app cannot
 * load (Draco or meshopt compression: the CSP forbids WebAssembly; embedded textures in a .glb are fine).
 *
 *   node scripts/dragon-prep.mjs <species-folder>        e.g. node scripts/dragon-prep.mjs wyvern
 *
 * Textures over 1024 px should be resized before shipping (the wyvern's are 2–4 K):
 *   npx @gltf-transform/cli resize public/dragons/wyvern/scene.gltf public/dragons/wyvern/scene.gltf --width 1024 --height 1024
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';

const folder = process.argv[2];
if (!folder) {
  console.error('usage: node scripts/dragon-prep.mjs <species-folder>');
  process.exit(1);
}
const dir = resolve(process.cwd(), 'public/dragons', folder);
const files = readdirSync(dir);
const model = files.find((f) => /\.gltf$/i.test(f)) ?? files.find((f) => /\.glb$/i.test(f));
if (!model) {
  console.error(`no .gltf or .glb in ${dir}`);
  process.exit(1);
}

/** The JSON part of a .gltf or the JSON chunk of a .glb. */
function readJson(path) {
  const buf = readFileSync(path);
  if (/\.glb$/i.test(path)) {
    const chunkLength = buf.readUInt32LE(12);
    return JSON.parse(buf.subarray(20, 20 + chunkLength).toString('utf8'));
  }
  return JSON.parse(buf.toString('utf8'));
}

const path = join(dir, model);
const json = readJson(path);
const kb = (n) => `${(n / 1024).toFixed(0)} KB`;

console.log(`${folder}/${model}  ${kb(statSync(path).size)}`);
console.log(`generator: ${json.asset?.generator ?? '?'}`);
const required = json.extensionsRequired ?? [];
if (required.some((e) => /draco|meshopt/i.test(e))) {
  console.log(`!! requires ${required.join(', ')}: the app cannot decode this (no WebAssembly under the CSP). Re-export without compression.`);
}
console.log('\nanimations:');
for (const a of json.animations ?? []) console.log(`  ${a.name ?? '(unnamed)'}`);
const names = (json.nodes ?? []).map((n) => n.name ?? '').filter(Boolean);
console.log(`\nnodes: ${names.length}`);
const heads = names.filter((n) => /head|jaw|mouth|neck|tongue/i.test(n));
console.log(`head / jaw candidates: ${heads.length ? heads.join(', ') : 'none (use mouthOffset in the species file)'}`);
console.log('\nimages:');
for (const img of json.images ?? []) {
  if (img.uri) {
    const p = join(dir, decodeURIComponent(img.uri));
    let size = '?';
    try {
      size = kb(statSync(p).size);
    } catch {
      size = 'missing';
    }
    console.log(`  ${img.uri}  ${size}`);
  } else {
    console.log(`  (embedded) ${img.mimeType ?? ''}`);
  }
}
const meshes = (json.meshes ?? []).length;
const materials = (json.materials ?? []).map((m) => m.name ?? '(unnamed)');
console.log(`\nmeshes: ${meshes}  materials: ${materials.join(', ')}`);
console.log('\nNext: map the clip names to fly / glide / idle / attack and the head / jaw bones in src/dragon/species.ts.');
