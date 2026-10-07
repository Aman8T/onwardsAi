/**
 * Smoke test: proves the Agent SDK can reach Claude with this machine's
 * Claude Code credentials, for both call shapes Tayyari uses.
 *   npm run smoke
 */
import { z } from 'zod';
import { MODELS, runStructured, streamChat } from '../claude.ts';

const t0 = Date.now();
const structured = await runStructured({
  model: MODELS.reader,
  system: 'You extract skills from text. Reply only through the structured output.',
  prompt: 'Text: "Built a React + TypeScript dashboard, wrote Vitest tests, deployed on Vercel."',
  schema: z.object({ skills: z.array(z.object({ name: z.string(), confidence: z.number() })) }),
});
console.log(`[haiku structured] ${Date.now() - t0}ms $${structured.costUsd.toFixed(4)}`, JSON.stringify(structured.data));

const t1 = Date.now();
let streamed = '';
const chat = await streamChat({
  system: 'You are a concise career coach. Answer in one short sentence.',
  prompt: 'Name one skill a frontend engineer should learn to move into AI product roles.',
  onText: (d) => {
    streamed += d;
    process.stdout.write(d);
  },
});
console.log(`\n[sonnet stream] ${Date.now() - t1}ms $${chat.costUsd.toFixed(4)} session=${chat.sessionId}`);

const t2 = Date.now();
const follow = await streamChat({
  system: 'You are a concise career coach. Answer in one short sentence.',
  prompt: 'What did you just recommend? Repeat it in three words.',
  resume: chat.sessionId,
  onText: (d) => process.stdout.write(d),
});
console.log(`\n[sonnet resume] ${Date.now() - t2}ms session=${follow.sessionId}`);
if (!streamed) throw new Error('no streamed text');
