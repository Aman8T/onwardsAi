/**
 * Capabilities parity: the Ask coach's one list of buttons lives in `shared/capabilities.ts` and is mirrored in
 * `backend/tayyari/contract/capabilities.py`. This script keeps the TypeScript list in `backend/tests/data/capabilities.json`;
 * `backend/tests/unit/contract/test_capabilities_parity.py` compares the Python list to that file field for field.
 *
 *   npm run check:capabilities             fails when the list no longer matches the stored file
 *   npm run check:capabilities -- --write  rewrites the file after a deliberate change to the list
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { CAN_SEE, CANNOT, CAPABILITIES } from '../shared/capabilities';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const FIXTURE = resolve(ROOT, 'backend/tests/data/capabilities.json');

const fresh = `${JSON.stringify({ capabilities: CAPABILITIES, canSee: CAN_SEE, cannot: CANNOT }, null, 2)}\n`;

if (process.argv.includes('--write')) {
  mkdirSync(dirname(FIXTURE), { recursive: true });
  writeFileSync(FIXTURE, fresh, 'utf8');
  console.log(`capabilities parity: wrote ${FIXTURE}`);
  process.exit(0);
}

if (!existsSync(FIXTURE)) {
  console.error('capabilities parity: the fixture is missing; run `npm run check:capabilities -- --write`');
  process.exit(1);
}
const stored = readFileSync(FIXTURE, 'utf8').replace(/\r\n/g, '\n');
if (stored !== fresh) {
  console.error(
    'capabilities parity: shared/capabilities.ts no longer gives the stored list. If the change is deliberate, mirror it in ' +
      'backend/tayyari/contract/capabilities.py and run `npm run check:capabilities -- --write`.',
  );
  process.exit(1);
}
console.log(`capabilities parity: ${CAPABILITIES.length} destinations match the stored list`);
