/**
 * Pre-computes the sample profile for instant, offline demos.
 *   npx tsx server/scripts/make-sample.ts   (suggested npm script: "make-sample")
 *
 * Runs the real pipeline (Haiku readers + Sonnet synthesizer) on the bundled Riya Kapoor fixtures and
 * writes server/sample/sample-profile.json (+ sample-context.json, so the chat has full context).
 */
import { writeFileSync } from 'node:fs';
import type { AnalysisEvent } from '../../shared/types.ts';
import { runAnalysis } from '../pipeline.ts';
import { SAMPLE_CONTEXT_PATH, SAMPLE_GOAL, SAMPLE_PROFILE_PATH } from '../sample/index.ts';
import { getContext } from '../store.ts';

const t0 = Date.now();
const stamp = () => `${((Date.now() - t0) / 1000).toFixed(1).padStart(6)}s`;
let lastChars = 0;

function print(e: AnalysisEvent) {
  switch (e.type) {
    case 'readers':
      console.log(`${stamp()}  readers: ${e.readers.map((r) => `${r.agent} (${r.src})`).join(', ')}`);
      break;
    case 'reader':
      if (e.reader.status === 'done' || e.reader.status === 'error') {
        console.log(`${stamp()}  [${e.reader.sourceId}] ${e.reader.status.toUpperCase()} · ${e.reader.line}`);
      }
      break;
    case 'signal':
      console.log(`${stamp()}    + ${e.label} (${e.src})`);
      break;
    case 'log':
      console.log(`${stamp()}  ${e.who.padEnd(8)} ${e.text}`);
      break;
    case 'phase':
      console.log(`${stamp()}  == ${e.phase}: ${e.label}`);
      break;
    case 'synth':
      if (e.chars && e.chars - lastChars < 2000 && e.step !== 3) break;
      lastChars = e.chars ?? lastChars;
      console.log(`${stamp()}  synth step ${e.step}${e.chars ? ` · ${e.chars} chars` : ''}`);
      break;
    case 'done':
      console.log(`${stamp()}  done · profile ${e.profile.id}`);
      break;
    case 'error':
      console.error(`${stamp()}  ERROR: ${e.message}`);
      break;
  }
}

const ctrl = new AbortController();
process.on('SIGINT', () => ctrl.abort());

try {
  const profile = await runAnalysis({ sample: true, links: [], goal: SAMPLE_GOAL }, print, ctrl.signal);
  writeFileSync(SAMPLE_PROFILE_PATH, `${JSON.stringify(profile, null, 2)}\n`);
  const context = await getContext(profile.id);
  if (context) writeFileSync(SAMPLE_CONTEXT_PATH, `${JSON.stringify(context)}\n`);
  console.log(`\nWrote ${SAMPLE_PROFILE_PATH}`);
  console.log(
    `${profile.person.name} · ${profile.person.headline} · mastery ${profile.mastery.score} · ` +
      `${profile.domains.length} domains · ${profile.skills.length} skills · ${profile.roles.length} roles`,
  );
  for (const r of profile.roles) console.log(`  ${String(r.alignment).padStart(3)}%  ${r.title} (${r.eta})`);
} catch (err) {
  console.error('\nmake-sample failed:', err instanceof Error ? err.message : err);
  process.exitCode = 1;
}
