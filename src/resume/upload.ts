/**
 * What a second file must be before it is sent: the same type and size rules as the first upload on the onboarding
 * screen (`src/views/Onboarding.tsx`, which keeps its own copy), so a file the first upload accepts is accepted here
 * and a bad one is refused on the page without a request. Plain words, no model involved.
 */
import { MAX_RESUME_FILES } from './types';
import type { UploadFailure } from './types';

export const MAX_BYTES = 10 * 1024 * 1024;
export const ACCEPT = '.pdf,.docx,.txt,.md';
export const ACCEPT_HINT = 'PDF, Word, text or Markdown, up to 10 MB';
const TYPES = new Set(['pdf', 'docx', 'txt', 'md']);

const extOf = (name: string): string => {
  const i = name.lastIndexOf('.');
  return i < 0 ? '' : name.slice(i + 1).toLowerCase();
};

/** "42 KB" or "1.3 MB". */
export function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export const NOTHING_ADDED = 'Nothing was added.';

export const WRONG_TYPE: UploadFailure = { message: `That file type is not supported. Use a PDF, DOCX, TXT or MD resume. ${NOTHING_ADDED}`, retry: false };
export const TOO_LARGE: UploadFailure = { message: `That file is too large. Resumes must be 10 MB or smaller. ${NOTHING_ADDED}`, retry: false };
export const UNREADABLE: UploadFailure = { message: `Leafout could not read any text in that file. Try a PDF, Word, text or Markdown file that holds your resume. ${NOTHING_ADDED}`, retry: false };
export const TOO_MANY: UploadFailure = { message: `You have ${MAX_RESUME_FILES} files here, the most Leafout keeps. ${NOTHING_ADDED}`, retry: false };
export const GENERIC: UploadFailure = { message: `Leafout could not add that file just now. ${NOTHING_ADDED}`, retry: true };

/** The failure for a file the page can refuse by itself, or null when it may be sent. */
export function checkFile(file: File): UploadFailure | null {
  const ext = extOf(file.name);
  if (ext === 'doc') return { message: `Legacy .doc files are not supported. Save it as DOCX or PDF and try again. ${NOTHING_ADDED}`, retry: false };
  if (!TYPES.has(ext)) return WRONG_TYPE;
  if (file.size === 0) return { message: `That file looks empty. Pick a different one. ${NOTHING_ADDED}`, retry: false };
  if (file.size > MAX_BYTES) return { message: `That file is ${formatSize(file.size)}. Resumes must be 10 MB or smaller. ${NOTHING_ADDED}`, retry: false };
  return null;
}
