/**
 * The dashboard's notes follow the account: the slips a person wrote to themselves and the lines they pinned
 * (`src/views/dashboard/notes.ts`). The browser keeps them per profile; a signed-in person also has them here, so they
 * are the same on every device.
 *
 *   GET /api/notes?profile=<id>   DeskNotes
 *   PUT /api/notes?profile=<id>   DeskNotes -> DeskNotes (replaces the whole list for that profile)
 *
 * Signed in only (401 otherwise); the profile must be the caller's own (another account's is a 404). The text is the
 * person's own words: stored as data, never interpreted and never read by a model.
 *
 * Mirrored by backend/tayyari/contract/notes.py; tests/unit/contract/test_notes_parity.py reads this file.
 */

export const NOTES_MAX = 12;
export const NOTE_TEXT_MAX = 240;
export const NOTE_LABEL_MAX = 60;
export const NOTE_BY_MAX = 120;
export const NOTE_ID_MAX = 64;

export type DeskNoteKind = 'own' | 'pinned';

export interface DeskNote {
  id: string;
  /** `own`: written on the desk. `pinned`: a line kept from under the greeting. */
  kind: DeskNoteKind;
  text: string;
  /** For a pinned line: its theme ("Quote", "Tip of the day"). */
  label?: string;
  /** For a pinned line: its small print (who said it). */
  by?: string;
  /** The client's timestamp in milliseconds; the newer one wins when two devices disagree. */
  at: number;
}

export interface DeskNotes {
  notes: DeskNote[];
}
