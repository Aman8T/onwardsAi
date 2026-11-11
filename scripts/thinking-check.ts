/**
 * Thinking-stream check: the lines file and the picker (src/views/analysis/).
 *   npm run check:thinking
 */
import { THINKING_LINES, type ThinkingPhase, type ThinkingType } from '../src/views/analysis/thinkingLines';
import { createPicker, fillLine, PLACEHOLDERS, type ThinkingValues } from '../src/views/analysis/thinking';

const TYPES: ThinkingType[] = ['verb', 'tip', 'quote', 'stat'];
const PHASES: ThinkingPhase[] = ['starting', 'reading', 'merge', 'score', 'rank'];
const MAX = 80;
const bad: string[] = [];

for (const l of THINKING_LINES) {
  const at = JSON.stringify(l.text);
  if (!TYPES.includes(l.type)) bad.push(`unknown type ${l.type}: ${at}`);
  if (!l.text.trim()) bad.push('empty text');
  if (l.text.length > MAX) bad.push(`${l.text.length} characters (max ${MAX}): ${at}`);
  if (l.when && !PHASES.includes(l.when)) bad.push(`unknown phase ${l.when}: ${at}`);
  for (const m of l.text.matchAll(/\{(\w+)\}/g)) {
    if (!(PLACEHOLDERS as readonly string[]).includes(m[1])) bad.push(`placeholder {${m[1]}} cannot be filled: ${at}`);
  }
}
for (const p of PHASES) {
  const verbs = THINKING_LINES.filter((l) => l.type === 'verb' && (!l.when || l.when === p)).length;
  if (verbs < 2) bad.push(`phase ${p} has ${verbs} verb(s), needs at least 2`);
}

const full: ThinkingValues = { source: 'Lovable', done: 2, sources: 4, chars: '1,200' };
const picker = createPicker(THINKING_LINES);
let last = '';
let lastType = '';
let run = 0;
for (let i = 0; i < 1000; i++) {
  const phase = PHASES[Math.floor(i / 50) % PHASES.length];
  const v = phase === 'starting' ? {} : full;
  const pick = picker.next(phase, v);
  if (!pick) {
    bad.push(`pick ${i} returned nothing in ${phase}`);
    break;
  }
  if (pick.text === last) {
    bad.push(`pick ${i} repeated ${JSON.stringify(pick.text)}`);
    break;
  }
  if (lastType !== '' && lastType !== 'verb' && pick.type !== 'verb') run++;
  last = pick.text;
  lastType = pick.type;
}
if (run > 0) bad.push(`${run} times two non-verbs ran back to back`);

if (createPicker([]).next('reading', full) !== null) bad.push('an empty list must return nothing');
if (fillLine('Reading {source}', {}) !== null) bad.push('a missing value must skip the line');

if (bad.length) {
  console.error(`thinking-check FAILED:\n- ${bad.join('\n- ')}`);
  process.exit(1);
}
console.log(`thinking-check OK: ${THINKING_LINES.length} lines, ${PHASES.length} phases, 1000 picks`);
