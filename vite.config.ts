import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { defineConfig, transformWithEsbuild } from 'vite';
import type { Plugin } from 'vite';
import react from '@vitejs/plugin-react';

const API_PORT = Number(process.env.TAYYARI_API_PORT ?? 8787);

const HUE_PATH = fileURLToPath(new URL('./src/theme/hue.js', import.meta.url));
const BOOT_PATH = fileURLToPath(new URL('./src/theme/boot.js', import.meta.url));

/**
 * Inlines the hue maths and the boot script in <head> before any stylesheet, so the first paint already has the right
 * hue, in dev and in dist/ alike. It goes right after <meta charset>, which must come within the first 1024 bytes, and
 * both sources are minified here (comments and whitespace out, identifiers kept): the page is served without a cache, so
 * its bytes count. The files are read at transform time so edits show up on the next reload. The server hashes the
 * inline script from the built index.html when it starts (backend/tayyari/api/security.py), so no hash is kept by hand.
 */
function calmBoot(): Plugin {
  return {
    name: 'tayyari:calm-boot',
    transformIndexHtml: {
      order: 'pre',
      async handler(html) {
        const hueSource = readFileSync(HUE_PATH, 'utf8').replace(/^export\s+/gm, '');
        const bootSource = readFileSync(BOOT_PATH, 'utf8');
        const { code } = await transformWithEsbuild(`${hueSource}\n${bootSource}`, 'calm-boot.js', {
          loader: 'js',
          target: 'es2018',
          minifyWhitespace: true,
          minifySyntax: true,
          legalComments: 'none',
        });
        const script = `<script>${code.trim()}</script>`;
        const charset = /<meta\s+charset="[^"]*"\s*\/?>/i;
        return charset.test(html)
          ? html.replace(charset, (tag) => `${tag}
    ${script}`)
          : html.replace(/<head>/i, (tag) => `${tag}
    ${script}`);
      },
    },
  };
}

export default defineConfig({
  plugins: [react(), calmBoot()],
  server: {
    port: Number(process.env.PORT ?? 5173),
    proxy: {
      '/api': { target: `http://localhost:${API_PORT}`, changeOrigin: true },
      // the radar card's public addresses (README image, LinkedIn picture, its page), served by the API
      '/r/': { target: `http://localhost:${API_PORT}`, changeOrigin: true },
      '/og/': { target: `http://localhost:${API_PORT}`, changeOrigin: true },
      '/u/': { target: `http://localhost:${API_PORT}`, changeOrigin: true },
    },
  },
});
