/**
 * Radar parity: the radar card (R1) is drawn in Python (`backend/tayyari/card/geometry.py`) from the same geometry the app
 * draws in `src/lib/radar.ts`. This script computes the app's paths for 1 to 8 axes at fixed values and keeps them in
 * `backend/tests/data/card/radar-paths.json`; `backend/tests/unit/card/test_geometry.py` checks the Python port against
 * that file, byte for byte.
 *
 *   npm run check:radar             fails when radar.ts no longer gives the stored paths (the fixture is stale)
 *   npm run check:radar -- --write  rewrites the fixture after a deliberate change to radar.ts
 *
 * Inside `npm run check`, so a change to the app's radar cannot leave the card drawing the old shape unnoticed.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { labelAnchor, polygonPath, radarGeom, radarLeaf, ringPath } from '../src/lib/radar';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const FIXTURE = resolve(ROOT, 'backend/tests/data/card/radar-paths.json');

/** Fixed, varied values per axis count: the edges (0 and 1), a third, and a spread in between. */
function valuesFor(n: number): number[] {
  return Array.from({ length: n }, (_, i) => {
    if (i === 0) return 1;
    if (i === 1) return 0;
    if (i === 2) return 1 / 3;
    return ((i * 37 + n * 11) % 100) / 100;
  });
}

function build() {
  const g = radarGeom();
  const cases = [];
  for (let n = 1; n <= 8; n++) {
    const values = valuesFor(n);
    cases.push({
      n,
      values,
      polygon: polygonPath(g, values),
      rings: [0.25, 0.5, 0.75, 1].map((level) => ringPath(g, n, level)),
      leaf: radarLeaf(g, n),
      labels: Array.from({ length: n }, (_, i) => labelAnchor(g, i, n)),
    });
  }
  return { geom: g, cases };
}

const fresh = `${JSON.stringify(build(), null, 2)}\n`;

if (process.argv.includes('--write')) {
  mkdirSync(dirname(FIXTURE), { recursive: true });
  writeFileSync(FIXTURE, fresh, 'utf8');
  console.log(`radar parity: wrote ${FIXTURE}`);
  process.exit(0);
}

if (!existsSync(FIXTURE)) {
  console.error('radar parity: the fixture is missing; run `npm run check:radar -- --write`');
  process.exit(1);
}
const stored = readFileSync(FIXTURE, 'utf8').replace(/\r\n/g, '\n');
if (stored !== fresh) {
  console.error(
    'radar parity: src/lib/radar.ts no longer gives the stored paths. If the change is deliberate, port it to ' +
      'backend/tayyari/card/geometry.py and run `npm run check:radar -- --write`.',
  );
  process.exit(1);
}
console.log('radar parity: radar.ts matches the card fixture (1 to 8 axes)');
