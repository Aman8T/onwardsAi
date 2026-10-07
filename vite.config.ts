import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import type { Plugin } from 'vite';
import react from '@vitejs/plugin-react';

const API_PORT = Number(process.env.TAYYARI_API_PORT ?? 8787);

const HUE_PATH = fileURLToPath(new URL('./src/theme/hue.js', import.meta.url));
const BOOT_PATH = fileURLToPath(new URL('./src/theme/boot.js', import.meta.url));

/**
 * Inlines the hue maths and the boot script at the top of <head> so the first
 * paint already has the right hue, in dev and in dist/ alike. The files are read
 * at transform time so edits show up on the next reload.
 */
function calmBoot(): Plugin {
  return {
    name: 'tayyari:calm-boot',
    transformIndexHtml() {
      const hueSource = readFileSync(HUE_PATH, 'utf8').replace(/^export\s+/gm, '');
      const bootSource = readFileSync(BOOT_PATH, 'utf8');
      return [{ tag: 'script', injectTo: 'head-prepend', children: `${hueSource}\n${bootSource}` }];
    },
  };
}

export default defineConfig({
  plugins: [react(), calmBoot()],
  server: {
    port: Number(process.env.PORT ?? 5173),
    proxy: {
      '/api': { target: `http://localhost:${API_PORT}`, changeOrigin: true },
    },
  },
});
