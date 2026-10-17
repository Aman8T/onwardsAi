// Emits the .d.ts tree the design-sync converter reads prop contracts from (cfg.buildCmd).
//
// tsc -p tsconfig.types.json writes declarations for ds-entry.ts, src/ and shared/ into ds-types/
// (gitignored). The converter globs `**/*.d.ts` with dot-directories excluded, so the entry's own
// declaration (ds-types/.design-sync/ds-entry.d.ts, which carries the `<Name>Props` aliases) is moved
// up to ds-types/ds-entry.d.ts with its relative imports re-pointed one level shallower.
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const out = join(root, 'ds-types');

rmSync(out, { recursive: true, force: true });
execFileSync(process.platform === 'win32' ? 'npx.cmd' : 'npx', ['tsc', '-p', join(here, 'tsconfig.types.json')], {
  cwd: root,
  stdio: 'inherit',
  shell: process.platform === 'win32',
});

const nested = join(out, '.design-sync', 'ds-entry.d.ts');
if (!existsSync(nested)) {
  console.error(`emit-types: expected ${nested} after tsc`);
  process.exit(1);
}
const text = readFileSync(nested, 'utf8').replace(/(['"])\.\.\//g, '$1./');
mkdirSync(out, { recursive: true });
writeFileSync(join(out, 'ds-entry.d.ts'), text);
rmSync(join(out, '.design-sync'), { recursive: true, force: true });
console.error(`emit-types: wrote ${join(out, 'ds-entry.d.ts')}`);
