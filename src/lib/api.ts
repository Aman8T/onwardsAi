import type { AnalysisEvent, ChatEvent, ChatRequest, Profile } from '../../shared/types';

/** Read a newline-delimited JSON response body as it streams in. */
async function readNdjson<T>(res: Response, onEvent: (e: T) => void): Promise<void> {
  if (!res.ok || !res.body) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.error) message = body.error;
    } catch {
      /* not JSON */
    }
    throw new Error(message);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf('\n')) !== -1) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) onEvent(JSON.parse(line) as T);
    }
  }
  const rest = buf.trim();
  if (rest) onEvent(JSON.parse(rest) as T);
}

export interface AnalyzeInput {
  file?: File | null;
  links: string[];
  goal?: string;
  notes?: string;
  /** Use the bundled sample persona (fixture sources, live AI). */
  sample?: boolean;
}

export async function analyze(input: AnalyzeInput, onEvent: (e: AnalysisEvent) => void, signal?: AbortSignal) {
  const form = new FormData();
  if (input.file) form.append('resume', input.file);
  form.append('links', JSON.stringify(input.links));
  if (input.goal) form.append('goal', input.goal);
  if (input.notes) form.append('notes', input.notes);
  if (input.sample) form.append('sample', '1');
  const res = await fetch('/api/analyze', { method: 'POST', body: form, signal });
  await readNdjson<AnalysisEvent>(res, onEvent);
}

export async function chat(req: ChatRequest, onEvent: (e: ChatEvent) => void, signal?: AbortSignal) {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
    signal,
  });
  await readNdjson<ChatEvent>(res, onEvent);
}

/** Pre-computed sample profile, for demos when live analysis is unavailable. */
export async function sampleProfile(): Promise<Profile> {
  const res = await fetch('/api/sample-profile');
  if (!res.ok) throw new Error('No pre-computed sample profile on the server');
  return res.json();
}

export async function fetchProfile(id: string): Promise<Profile | null> {
  const res = await fetch(`/api/profile/${encodeURIComponent(id)}`);
  return res.ok ? res.json() : null;
}

export interface Health {
  ok: boolean;
  models: { reader: string; synth: string; chat: string };
  hasSample: boolean;
}

export async function health(): Promise<Health | null> {
  try {
    const res = await fetch('/api/health');
    return res.ok ? res.json() : null;
  } catch {
    return null;
  }
}
