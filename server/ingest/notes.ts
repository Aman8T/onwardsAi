import type { RawSource } from './types.ts';
import { findTerms } from './terms.ts';

const MAX_NOTES_CHARS = 20_000;

/** Free-text context the user pasted (LinkedIn About section, goals, anything). Never fails. */
export function ingestNotes(text: string): RawSource {
  const content = (text ?? '')
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
    .slice(0, MAX_NOTES_CHARS);

  return {
    id: 'notes',
    kind: 'notes',
    label: 'Notes',
    src: 'Pasted context',
    kindLabel: 'Extra context',
    content,
    facts: { chars: content.length, words: content ? content.split(/\s+/).length : 0 },
    quickSignals: findTerms(content, 8),
  };
}
