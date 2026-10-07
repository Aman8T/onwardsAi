/** Pure helpers for the chat view: copy, labels, suggestions, tool text. */
import type { ChatOrigin, ChatOriginKind, Profile } from '../../../shared/types';
import type { ChatMessage, ChatTool, Thread } from '../../store';

/** Short kind label shown under a thread title ("role", "topic", "source"…). */
export const THREAD_KIND: Record<ChatOriginKind, string> = {
  role: 'role',
  skill: 'topic',
  domain: 'topic',
  requirement: 'topic',
  source: 'source',
  you: 'overview',
  free: 'question',
};

/** Which graph an origin lives on, for the "Started from" card subtitle. */
export const ORIGIN_WHERE: Record<ChatOriginKind, string> = {
  role: 'Next roles',
  requirement: 'Next roles',
  skill: 'Your graph',
  domain: 'Your graph',
  you: 'Your graph',
  source: 'Sources',
  free: 'Open question',
};

export function threadTitle(t: Thread): string {
  if (t.origin.kind === 'free') {
    const first = t.messages.find((m) => m.role === 'user');
    if (first) return first.text.replace(/\s+/g, ' ').slice(0, 90);
  }
  return t.origin.title;
}

const SUGGESTIONS: Record<'role' | 'topic' | 'github' | 'other' | 'open', string[]> = {
  role: [
    'Turn this into a weekly calendar',
    'Which repo should I start with?',
    'Rewrite my resume headline for this pivot',
    'What will the interview loop test?',
  ],
  topic: [
    'Give me a first project for this',
    'Find the best free resources',
    'How will I know I have levelled up?',
    'What does great look like here?',
  ],
  github: ['Which repos should I pin?', 'Write a README for my best repo', 'What is missing from my profile?'],
  other: ['What should I fix first?', 'Rewrite it for my next role'],
  open: ['What should I focus on this month?', 'Which role suits me best?', 'Where am I underselling myself?'],
};

export function suggestionsFor(origin: ChatOrigin, profile: Profile): string[] {
  switch (origin.kind) {
    case 'role':
      return SUGGESTIONS.role;
    case 'skill':
    case 'domain':
    case 'requirement':
      return SUGGESTIONS.topic;
    case 'source': {
      const src = profile.sources.find((s) => s.id === origin.refId);
      const isGithub = src ? src.kind === 'github' : /github/i.test(origin.title);
      return isGithub ? SUGGESTIONS.github : SUGGESTIONS.other;
    }
    default:
      return SUGGESTIONS.open;
  }
}

/** "github.com/user/repo" from a URL; falls back to the raw detail. */
function hostPath(detail: string): string {
  const raw = detail.trim();
  if (!raw) return 'a page';
  let out = raw;
  try {
    const u = new URL(/^https?:\/\//i.test(raw) ? raw : `https://${raw}`);
    out = `${u.host.replace(/^www\./, '')}${u.pathname === '/' ? '' : u.pathname.replace(/\/$/, '')}`;
  } catch {
    /* not a URL — show it as-is */
  }
  return out.length > 48 ? `${out.slice(0, 47)}…` : out;
}

/** Human text for a tool chip. */
export function toolText(tool: ChatTool): string {
  const detail = tool.detail.trim();
  if (tool.name === 'WebSearch') return detail ? `Searched the web · ${detail}` : 'Searched the web';
  if (tool.name === 'WebFetch') return `Read ${hostPath(detail)}`;
  return tool.name;
}

/** Rotating status lines while the first token has not arrived. */
export function statusLines(profile: Profile): string[] {
  const kinds = new Set(profile.sources.filter((s) => s.ok).map((s) => s.kind));
  const lines: string[] = [];
  if (kinds.has('resume')) lines.push('Reading your resume…');
  if (kinds.has('github') || kinds.has('lovable')) lines.push('Cross-checking your repos…');
  lines.push('Looking through your skill graph…', 'Drafting your plan…');
  return lines;
}

/** "your resume, GitHub, Lovable and your skill graph" */
export function sourcesPhrase(profile: Profile): string {
  const ok = profile.sources.filter((s) => s.ok);
  const labels = [...new Set(ok.filter((s) => s.kind !== 'resume').map((s) => s.label))];
  const shown = labels.slice(0, 4);
  const extra = labels.length - shown.length;
  const parts: string[] = [];
  if (ok.some((s) => s.kind === 'resume')) parts.push('your resume');
  parts.push(...shown);
  if (extra > 0) parts.push(`${extra} more`);
  parts.push('your skill graph');
  return parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
}

/**
 * Hide the failed reply and the repeated ask after a Retry, so the thread
 * reads user → answer instead of user → error → user → answer.
 */
export function collapseRetries(messages: ChatMessage[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    const prev = messages[i - 1];
    const prev2 = messages[i - 2];
    if (m.role === 'user' && prev?.role === 'assistant' && prev.status === 'error' && prev2?.role === 'user' && prev2.text === m.text) {
      if (out[out.length - 1] === prev) out.pop();
      continue;
    }
    out.push(m);
  }
  return out;
}
