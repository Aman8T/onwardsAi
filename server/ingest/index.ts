/** Deterministic source ingestion (no AI). The pipeline imports everything from here. */
export { classifyUrl, isKnownPlatform, normalizeUrl } from './classify.ts';
export type { ClassifiedUrl } from './classify.ts';
export { ingestResume } from './resume.ts';
export { ingestGithub } from './github.ts';
export { ingestWeb } from './web.ts';
export { ingestNotes } from './notes.ts';
export { countTerms, findTerms, TERM_COUNT } from './terms.ts';
export type { TermHit } from './terms.ts';
export type { RawSource, IngestLog, UploadedFile } from './types.ts';
