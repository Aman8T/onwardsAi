/**
 * Career chat: one streamed turn of a thread. Claude gets the whole profile (graph + reader findings +
 * raw source excerpts) and the node the user clicked, and continues the same Agent SDK session on
 * follow-up turns.
 */
import type { ChatEvent, ChatRequest } from '../shared/types.ts';
import { MODELS, streamChat } from './claude.ts';
import { humanizeError, isAbortError } from './pipeline.ts';
import { buildChatSystem } from './prompts.ts';
import { getContext, getProfile } from './store.ts';

/**
 * True when the CLI rejected `--resume` before reaching the model: the session is missing,
 * unloadable, or held by another process. (Callers only retry when nothing was emitted yet.)
 */
function isResumeFailure(err: unknown): boolean {
  const detail = (err as { detail?: { num_turns?: number; errors?: unknown[] } } | undefined)?.detail;
  const m = err instanceof Error ? `${err.message} ${JSON.stringify(detail ?? '')}` : String(err);
  if (/no conversation found|session.{0,40}(not found|does not exist|missing|invalid)|could not (find|resume)|unknown session|ENOENT.*\.jsonl/i.test(m)) return true;
  if (/failed to resume|unable to load transcript|session load failed|running in the background/i.test(m)) return true;
  // an error result with zero turns means the CLI never got past session setup
  return detail?.num_turns === 0 && Array.isArray(detail.errors) && detail.errors.length > 0;
}

export async function runChat(req: ChatRequest, emit: (e: ChatEvent) => void, signal: AbortSignal): Promise<void> {
  const profile = await getProfile(req.profileId);
  if (!profile) {
    emit({ type: 'error', message: 'Profile not found — run the analysis again' });
    return;
  }
  const context = (await getContext(req.profileId)) ?? { sources: [], readers: {} };
  const system = buildChatSystem({
    profile,
    origin: req.origin,
    sources: context.sources,
    readers: context.readers,
    budget: 60000,
  });

  let emitted = false;
  const turn = (prompt: string, resume?: string) =>
    streamChat({
      system,
      prompt,
      resume,
      model: MODELS.chat,
      signal,
      onText: (delta) => {
        if (!delta || signal.aborted) return;
        emitted = true;
        emit({ type: 'delta', text: delta });
      },
      onTool: (name, detail) => {
        if (signal.aborted) return;
        emit({ type: 'tool', name, detail: detail.slice(0, 200) });
      },
    });

  try {
    let result;
    try {
      result = await turn(req.message, req.sessionId || undefined);
    } catch (err) {
      if (!req.sessionId || emitted || signal.aborted || !isResumeFailure(err)) throw err;
      console.warn(`[chat] session ${req.sessionId} could not be resumed, starting fresh`);
      result = await turn(
        `(Note: the earlier messages of this conversation are no longer available — answer this on its own, using the profile.)\n\n${req.message}`,
      );
    }
    if (!signal.aborted) emit({ type: 'done', sessionId: result.sessionId });
  } catch (err) {
    if (signal.aborted || isAbortError(err)) return;
    console.error('[chat]', err);
    emit({ type: 'error', message: humanizeError(err) });
  }
}
