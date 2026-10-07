import type { SourceKind } from '../../shared/types.ts';

/**
 * A source after deterministic fetching/extraction, before any AI reads it.
 * Produced by server/ingest/*, consumed by server/pipeline.ts (Haiku readers).
 */
export interface RawSource {
  /** Stable id used everywhere downstream: "resume", "github", "lovable-1", "portfolio-1", "link-2", "notes". */
  id: string;
  kind: SourceKind;
  /** Display label: "Resume", "GitHub", "Lovable", "Portfolio", "Kaggle", "LinkedIn", "Notes". */
  label: string;
  url?: string;
  /** What to show as the source: file name, or URL without protocol. */
  src: string;
  /** "Resume · PDF", "GitHub profile", "Lovable app", "Portfolio site", "Web page". */
  kindLabel: string;
  /** Text prepared for the reader agent, already trimmed to a sane budget (≤ ~40k chars). */
  content: string;
  /** Deterministic facts gathered while fetching (e.g. { repos: 34, languages: ['TypeScript', …] }). */
  facts: Record<string, unknown>;
  /** Cheap signals found without AI (languages, libraries detected…) — streamed to the UI early. */
  quickSignals: string[];
  /** When text could not be extracted (e.g. a scanned PDF): absolute path for the reader's Read tool. */
  filePath?: string;
  /** Set when fetching failed; the pipeline still shows the source, marked as failed. */
  error?: string;
}

/** Progress callback: one short, human line per step ("Fetched profile, 34 public repos"). */
export type IngestLog = (text: string) => void;

export interface UploadedFile {
  path: string;
  originalName: string;
  mimetype: string;
  size: number;
}
