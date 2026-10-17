/**
 * The words of the share dialog, kept apart from the markup: the referral note as one editable text, the connection's
 * name filled into it live, what is sent, and the key under it.
 *
 * The note is held as a template that contains `[name]` where the connection's first name goes. What the person sees in
 * the textarea is the template with the name filled in (`fill`); what they type is turned back into the template
 * (`edit`), so changing the name afterwards still reaches every `[name]` they left in place, and never touches a word
 * they wrote that happens to look like the name. What leaves the app (`outgoing`) never has a `[name]` left in it: with
 * no name given, the greeting reads "Hi there,".
 */
import type { ReferralLine, ReferralNote, ReferralPart } from '../../../shared/jobsearch';
import { joinNames } from '../prepare/words';

export const NAME = '[name]';

/** What stands in for the name in what is copied or shared when the To field was left empty ("Hi there,"). */
export const FALLBACK_NAME = 'there';

/** The greeting that opens the note. */
export const GREETING = `Hi ${NAME},`;

/** The longest first name taken: a longer one is not a first name. */
const NAME_MAX = 40;

/** A first name as typed: spaces tidied, control characters and square brackets left out, short. */
export function cleanName(raw: string): string {
  return raw
    .replace(/[\u0000-\u001f\u007f[\]]/g, '')
    .replace(/\s+/g, ' ')
    .trimStart()
    .slice(0, NAME_MAX);
}

/** The template with `name` where `[name]` stands. An empty name leaves the placeholder. */
export function fill(template: string, name: string): string {
  const n = name.trim();
  return n ? template.split(NAME).join(n) : template;
}

/**
 * The template as it leaves the app (copied, or handed to the share sheet): every `[name]` is the name, or "there" when
 * none was given, so no placeholder is ever sent ("Hi there,").
 */
export function outgoing(template: string, name: string): string {
  return fill(template, name.trim() || FALLBACK_NAME);
}

/**
 * Where a position in the filled text falls in the template, as [templateOffset, filledOffset]. A position inside a filled
 * name is moved out to the edge of the whole name (`side` says which edge) so a token is replaced whole or not at all.
 */
function locate(template: string, name: string, at: number, side: 'start' | 'end'): [number, number] {
  let t = 0;
  let d = 0;
  while (t < template.length) {
    if (template.startsWith(NAME, t)) {
      const end = d + name.length;
      if (at <= d) break;
      if (at < end) return side === 'start' ? [t, d] : [t + NAME.length, end];
      t += NAME.length;
      d = end;
      continue;
    }
    if (d >= at) break;
    t += 1;
    d += 1;
  }
  return [t, d];
}

/**
 * The template after the person typed `next` into a textarea that showed `fill(template, name)`. Only the span that
 * changed is rewritten, so every other `[name]` stays a placeholder.
 */
export function edit(template: string, name: string, next: string): string {
  const n = name.trim();
  if (!n || !template.includes(NAME)) return next;
  const prev = fill(template, n);
  if (next === prev) return template;
  const max = Math.min(prev.length, next.length);
  let head = 0;
  while (head < max && prev.charCodeAt(head) === next.charCodeAt(head)) head += 1;
  let tail = 0;
  while (tail < max - head && prev.charCodeAt(prev.length - 1 - tail) === next.charCodeAt(next.length - 1 - tail)) tail += 1;
  const [from, shownFrom] = locate(template, n, head, 'start');
  const [to, shownTo] = locate(template, n, prev.length - tail, 'end');
  return template.slice(0, from) + next.slice(shownFrom, next.length - (prev.length - shownTo)) + template.slice(to);
}

/**
 * The note as one text: "Hi [name]," and a blank line, then one paragraph (the sentences in order, then the ask, joined by
 * single spaces). The link goes on a line of its own after it (`withLink`).
 */
export function compose(note: Pick<ReferralNote, 'lines' | 'ask'>): string {
  const paragraph = [...note.lines.map((l) => l.text.trim()), note.ask.trim()].filter(Boolean).join(' ');
  return `${GREETING}\n\n${paragraph}`;
}

/** What gets copied or shared: the note, then the link on a line of its own. */
export const withLink = (text: string, link: string) => `${text.trim()}\n\n${link}`;

/** "70 words": how long the message is, greeting and ask included. */
export function countWords(text: string): string {
  const words = text.split(/\s+/).filter(Boolean).length;
  return `${words} ${words === 1 ? 'word' : 'words'}`;
}

/** What a sentence of the note is for, in the key under it. */
export function partLabel(part: ReferralPart): string {
  return part === 'intro' ? 'Who you are' : part === 'fit' ? 'Why you fit' : 'Why them';
}

/** "the posting and your resume": where a sentence's words come from. */
export const sourcesText = (line: Pick<ReferralLine, 'sources'>) => (line.sources.length > 0 ? joinNames(line.sources) : 'the posting and your profile');

/**
 * One line of the key under the note, "What each sentence draws on": "1 · Who you are · your resume",
 * "2 · Why you fit · the match: the posting and your resume", "3 · Why them · the posting".
 */
export function keyLine(line: Pick<ReferralLine, 'part' | 'sources'>, index: number): string {
  const from = sourcesText(line);
  return `${index + 1} · ${partLabel(line.part)} · ${line.part === 'fit' ? `the match: ${from}` : from}`;
}
