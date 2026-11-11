/** The dashboard notes' two calls (`shared/notes.ts`). Signed in only; any failure throws and the caller stays quiet. */
import type { DeskNote, DeskNotes } from '../../../shared/notes';

const url = (profileId: string) => `/api/notes?profile=${encodeURIComponent(profileId)}`;

export async function getNotes(profileId: string, signal?: AbortSignal): Promise<DeskNote[]> {
  const res = await fetch(url(profileId), { signal });
  if (!res.ok) throw new Error(`notes ${res.status}`);
  const body: DeskNotes = await res.json();
  return Array.isArray(body.notes) ? body.notes : [];
}

export async function putNotes(profileId: string, notes: readonly DeskNote[], signal?: AbortSignal): Promise<void> {
  const body: DeskNotes = { notes: [...notes] };
  const res = await fetch(url(profileId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error(`notes ${res.status}`);
}
