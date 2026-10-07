import { copyFile, readFile } from 'node:fs/promises';
import path from 'node:path';
import mammoth from 'mammoth';
import { extractText, getDocumentProxy } from 'unpdf';
import type { IngestLog, RawSource, UploadedFile } from './types.ts';
import { findTerms } from './terms.ts';

type ResumeFormat = 'pdf' | 'docx' | 'text';

const FORMAT_LABEL: Record<ResumeFormat, string> = { pdf: 'PDF', docx: 'DOCX', text: 'Text' };
const MAX_RESUME_CHARS = 30_000;
/** Below this we assume the document is scanned/image-based and hand the file to the reader's Read tool. */
const MIN_TEXT_CHARS = 200;

/* ───────────────────────── helpers ───────────────────────── */

function detectFormat(file: UploadedFile, head: Uint8Array): ResumeFormat | 'doc' | null {
  const ext = path.extname(file.originalName || '').toLowerCase();
  if (ext === '.pdf') return 'pdf';
  if (ext === '.docx') return 'docx';
  if (ext === '.doc') return 'doc';
  if (ext === '.txt' || ext === '.md' || ext === '.markdown' || ext === '.text') return 'text';

  const mime = (file.mimetype || '').toLowerCase();
  if (mime === 'application/pdf') return 'pdf';
  if (mime.includes('wordprocessingml')) return 'docx';
  if (mime === 'application/msword') return 'doc';
  if (mime.startsWith('text/')) return 'text';

  // Last resort: sniff the magic bytes.
  const ascii = String.fromCharCode(...head.slice(0, 5));
  if (ascii.startsWith('%PDF')) return 'pdf';
  if (head[0] === 0x50 && head[1] === 0x4b) return 'docx';
  return null;
}

/** Normalise whitespace and strip invisible junk that PDF/DOCX extraction leaves behind. */
function cleanText(input: string): string {
  let s = input;
  try {
    s = s.normalize('NFKC'); // ligatures (ﬁ → fi), full-width forms, odd spaces
  } catch {
    /* keep the raw string */
  }
  return s
    .replace(/\r\n?/g, '\n')
    .replace(/\f/g, '\n')
    .replace(/[\u00a0\u2007\u202f]/g, ' ')
    .replace(/[\u200b-\u200d\u2060\ufeff]/g, '')
    .replace(/[\u0000-\u0008\u000b\u000e-\u001f]/g, '')
    .replace(/[ \t]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

const SECTION_PATTERNS: Array<[string, RegExp]> = [
  ['Summary', /^(?:(?:professional|career|executive) )?(?:summary|profile|objective|about(?: me)?)$/],
  [
    'Experience',
    /^(?:(?:professional|work|relevant|industry|employment|internship|internships) )?(?:experience|history)$|^employment$|^internships?$/,
  ],
  ['Projects', /^(?:(?:personal|academic|selected|key|side|notable|technical) )?projects?$/],
  [
    'Skills',
    /^(?:(?:technical|core|key|professional) )?skills?(?: (?:& |and )?(?:tools|technologies|expertise))?$|^(?:tech(?:nical)? stack|technologies|tools(?: (?:& |and )technologies)?|competencies|expertise)$/,
  ],
  ['Education', /^education(?: (?:& |and )?(?:training|certifications?))?$|^academics?$|^academic background$/],
  [
    'Certifications',
    /^(?:certifications?|licen[sc]es?(?: (?:& |and )?certifications?)?|courses|training|courses (?:& |and )certifications?)$/,
  ],
  [
    'Achievements',
    /^(?:achievements?|awards?(?: (?:& |and )?(?:honou?rs|achievements?|recognition))?|honou?rs(?: (?:& |and )awards)?|accomplishments|recognition)$/,
  ],
  ['Publications', /^(?:publications?|research|papers)$/],
  ['Open Source', /^open[- ]source(?: contributions?)?$/],
  [
    'Leadership',
    /^(?:leadership|extracurriculars?|extra[- ]curricular activities|activities|positions of responsibility|volunteer(?:ing)?(?: experience)?|community)$/,
  ],
  ['Languages', /^languages$/],
  ['Interests', /^(?:interests|hobbies)$/],
];

/** Section headings found in the text, in document order ("Experience", "Projects", "Skills"…). */
function detectSections(text: string): string[] {
  const found: string[] = [];
  for (const rawLine of text.split('\n')) {
    const line = rawLine
      .trim()
      .replace(/^[\s\-–—•·*#|:_=~\u2022]+/, '')
      .replace(/[\s:\-–—|_=~]+$/, '')
      .toLowerCase();
    if (!line || line.length > 45) continue;
    for (const [name, re] of SECTION_PATTERNS) {
      if (re.test(line)) {
        if (!found.includes(name)) found.push(name);
        break;
      }
    }
  }
  return found;
}

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`;

function failure(file: UploadedFile, format: ResumeFormat | null, error: string, extra: Partial<RawSource> = {}): RawSource {
  return {
    id: 'resume',
    kind: 'resume',
    label: 'Resume',
    src: file.originalName || 'resume',
    kindLabel: format ? `Resume · ${FORMAT_LABEL[format]}` : 'Resume',
    content: '',
    facts: {},
    quickSignals: [],
    error,
    ...extra,
  };
}

/** The reader's Read tool decides by extension, so make sure an upload saved without one gets one. */
async function withExtension(uploadPath: string, ext: string): Promise<string> {
  const abs = path.resolve(uploadPath);
  if (path.extname(abs).toLowerCase() === ext) return abs;
  try {
    const copy = abs + ext;
    await copyFile(abs, copy);
    return copy;
  } catch {
    return abs;
  }
}

/* ───────────────────────── extractors ───────────────────────── */

async function extractPdf(buffer: Buffer): Promise<{ text: string; pages: number }> {
  const pdf = await getDocumentProxy(new Uint8Array(buffer));
  try {
    const { totalPages, text } = await extractText(pdf, { mergePages: true });
    return { text, pages: totalPages };
  } finally {
    await pdf.loadingTask.destroy().catch(() => undefined);
  }
}

/* ───────────────────────── public API ───────────────────────── */

export async function ingestResume(file: UploadedFile, log: IngestLog, signal?: AbortSignal): Promise<RawSource> {
  signal?.throwIfAborted();
  let buffer: Buffer;
  try {
    buffer = await readFile(file.path);
  } catch {
    return failure(file, null, 'Could not read the uploaded resume file. Please upload it again.');
  }
  if (buffer.length === 0) return failure(file, null, 'That file is empty. Please upload your resume again.');

  const format = detectFormat(file, buffer.subarray(0, 8));
  if (format === 'doc') {
    return failure(file, null, 'Legacy .doc files are not supported. Save the resume as DOCX or PDF and upload it again.');
  }
  if (!format) {
    return failure(file, null, 'Unsupported file type. Upload your resume as a PDF, DOCX or TXT file.');
  }

  let raw = '';
  let pages = 0;
  signal?.throwIfAborted(); // the extractors below are CPU-bound; skip them once the client has gone
  try {
    if (format === 'pdf') {
      const out = await extractPdf(buffer);
      raw = out.text;
      pages = out.pages;
      log(`Parsed ${plural(pages, 'page')}`);
    } else if (format === 'docx') {
      const out = await mammoth.extractRawText({ buffer });
      raw = out.value.replace(/\n{2,}/g, '\n'); // mammoth separates every paragraph by a blank line
    } else {
      raw = buffer.toString('utf8');
    }
  } catch (err) {
    const msg = err instanceof Error ? `${err.name} ${err.message}` : String(err);
    if (/password/i.test(msg)) {
      return failure(file, format, 'That PDF is password-protected. Export an unlocked copy and upload it again.');
    }
    return failure(
      file,
      format,
      format === 'pdf'
        ? 'Could not read that PDF. It may be corrupted. Try exporting it again or upload a DOCX.'
        : format === 'docx'
          ? 'Could not read that DOCX file. It may be corrupted. Try exporting it again or upload a PDF.'
          : 'Could not read that text file.',
    );
  }

  // Only the first MAX_RESUME_CHARS are analysed and returned; section detection, term finding and word
  // counts on a 10 MB text upload would otherwise stall the event loop for seconds. The full length is
  // kept for the facts and for the scanned-PDF threshold below.
  const full = cleanText(raw);
  const text = full.slice(0, MAX_RESUME_CHARS);
  const chars = full.length;
  const words = text ? text.split(/\s+/).length : 0;
  if (format !== 'pdf') {
    pages = Math.max(1, Math.ceil(chars / 3200));
    log(format === 'docx' ? `Read ${words.toLocaleString('en-US')} words from the document` : `Read ${words.toLocaleString('en-US')} words of text`);
  }

  const sections = detectSections(text);
  if (sections.length > 0) log(`Found sections: ${sections.join(', ')}`);

  const quickSignals = findTerms(text, 8);
  if (quickSignals.length > 0) log(`Spotted ${plural(quickSignals.length, 'skill')}: ${quickSignals.join(', ')}`);

  const facts: Record<string, unknown> = {
    pages,
    chars,
    words,
    sections,
    format: FORMAT_LABEL[format],
    ...(chars > MAX_RESUME_CHARS ? { truncated: true } : {}),
  };

  const base = {
    id: 'resume',
    kind: 'resume' as const,
    label: 'Resume',
    src: file.originalName || 'resume',
    kindLabel: `Resume · ${FORMAT_LABEL[format]}`,
    facts,
    quickSignals,
  };

  if (chars < MIN_TEXT_CHARS) {
    if (format === 'pdf') {
      // Scanned or image-only PDF: let the reader agent look at the file itself.
      log('Little selectable text found, the reader will view the file directly');
      const filePath = await withExtension(file.path, '.pdf');
      return {
        ...base,
        facts: { ...facts, scanned: true },
        filePath,
        content:
          'The text of this resume could not be extracted (it looks like a scanned or image-based PDF). ' +
          'Open the file at the path provided with the Read tool and read the resume from it.' +
          (text ? `\n\nPartial text that could be extracted:\n${text}` : ''),
      };
    }
    if (chars === 0) {
      return failure(file, format, 'No readable text was found in that file. Try a different export of your resume.', {
        facts,
      });
    }
  }

  return { ...base, content: text };
}
