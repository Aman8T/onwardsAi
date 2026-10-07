/**
 * Tayyari HTTP API.
 *
 *   POST /api/analyze        multipart (resume?, links, goal?, notes?, sample?) → NDJSON stream of AnalysisEvent
 *   POST /api/chat           JSON ChatRequest → NDJSON stream of ChatEvent
 *   GET  /api/profile/:id    a saved Profile
 *   GET  /api/sample-profile the pre-computed sample Profile (offline demo)
 *   GET  /api/health         { ok, models, hasSample }
 *
 * When a production build exists in dist/, the web app is served too (SPA fallback).
 */
import express, { type NextFunction, type Request, type RequestHandler, type Response } from 'express';
import multer from 'multer';
import { existsSync, mkdirSync } from 'node:fs';
import { readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { z } from 'zod';
import type { AnalysisEvent, ChatEvent } from '../shared/types.ts';
import { runChat } from './chat.ts';
import { MODELS } from './claude.ts';
import type { UploadedFile } from './ingest/types.ts';
import { AnalysisFailed, humanizeError, runAnalysis, usableLinks } from './pipeline.ts';
import { SAMPLE_PROFILE_PATH, hasSampleProfile } from './sample/index.ts';
import { getProfile } from './store.ts';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DIST = join(ROOT, 'dist');
const UPLOAD_DIR = join(tmpdir(), 'tayyari-uploads');
const PORT = Number(process.env.TAYYARI_API_PORT ?? 8787);

mkdirSync(UPLOAD_DIR, { recursive: true });

class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'HttpError';
  }
}

/* ───────────────────────── helpers ───────────────────────── */

const ACCEPTED_EXT = new Set(['.pdf', '.docx', '.txt', '.md']);
const ACCEPTED_MIME = new Set([
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'text/plain',
  'text/markdown',
  'text/x-markdown',
]);

const upload = multer({
  dest: UPLOAD_DIR,
  limits: { fileSize: 10 * 1024 * 1024, files: 1, fields: 20, fieldSize: 256 * 1024 },
  fileFilter: (_req, file, cb) => {
    const ext = extname(file.originalname).toLowerCase();
    if (ACCEPTED_EXT.has(ext) || (!ext && ACCEPTED_MIME.has(file.mimetype))) cb(null, true);
    else cb(new HttpError(400, 'Upload your resume as a PDF, DOCX, TXT or Markdown file.'));
  },
});

/** multer with its errors turned into clean 4xx responses. */
const resumeUpload: RequestHandler = (req, res, next) => {
  upload.single('resume')(req, res, (err: unknown) => {
    if (!err) return next();
    if (err instanceof multer.MulterError) {
      return next(
        err.code === 'LIMIT_FILE_SIZE'
          ? new HttpError(413, 'That file is larger than 10 MB.')
          : new HttpError(400, err.code === 'LIMIT_UNEXPECTED_FILE' ? 'Only one file, in the "resume" field, is accepted.' : `Upload error: ${err.message}`),
      );
    }
    next(err);
  });
};

/** Remove an upload and anything the ingester derived from it (e.g. "<name>.pdf"). */
async function removeUpload(path: string | undefined) {
  if (!path) return;
  const dir = dirname(path);
  const base = basename(path);
  try {
    const names = await readdir(dir);
    await Promise.all(names.filter((n) => n.startsWith(base)).map((n) => rm(join(dir, n), { force: true })));
  } catch {
    await rm(path, { force: true }).catch(() => undefined);
  }
}

function parseLinks(value: unknown): string[] {
  const values = Array.isArray(value) ? value : [value];
  const out: string[] = [];
  for (const v of values) {
    if (typeof v !== 'string' || !v.trim()) continue;
    let parsed: unknown = undefined;
    try {
      parsed = JSON.parse(v);
    } catch {
      parsed = v.split(/[\s,]+/);
    }
    const list = Array.isArray(parsed) ? parsed : [parsed];
    for (const item of list) if (typeof item === 'string' && item.trim()) out.push(item.trim().slice(0, 500));
  }
  return out.slice(0, 20);
}

const field = (v: unknown, max: number): string | undefined => (typeof v === 'string' && v.trim() ? v.trim().slice(0, max) : undefined);

/** Start an NDJSON response: one JSON object per line, flushed immediately; aborts when the client goes away. */
function ndjson<E extends { type: string }>(res: Response) {
  res.status(200);
  res.setHeader('Content-Type', 'application/x-ndjson; charset=utf-8');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders();
  res.socket?.setNoDelay(true);

  const ctrl = new AbortController();
  let finished = false;
  let terminal = false;
  res.on('close', () => {
    if (!finished) ctrl.abort();
  });
  // Blank lines keep idle proxies from closing long, quiet streams; the client skips them.
  const heartbeat = setInterval(() => {
    if (!res.writableEnded && !res.destroyed) res.write('\n');
  }, 15000);

  return {
    signal: ctrl.signal,
    get terminal() {
      return terminal;
    },
    send(e: E) {
      if (e.type === 'done' || e.type === 'error') terminal = true;
      if (res.writableEnded || res.destroyed) return;
      res.write(`${JSON.stringify(e)}\n`);
    },
    end() {
      finished = true;
      clearInterval(heartbeat);
      if (!res.writableEnded) res.end();
    },
  };
}

/* ───────────────────────── app ───────────────────────── */

const app = express();
app.disable('x-powered-by');

app.use((req, res, next) => {
  const t0 = performance.now();
  res.on('close', () => {
    const ms = Math.round(performance.now() - t0);
    console.log(`${req.method} ${req.originalUrl} ${res.statusCode} ${ms}ms${res.writableFinished ? '' : ' (client closed)'}`);
  });
  next();
});

app.get('/api/health', (_req, res) => {
  res.json({ ok: true, models: MODELS, hasSample: hasSampleProfile() });
});

app.post('/api/analyze', resumeUpload, async (req, res) => {
  const file = req.file;
  const body = (req.body ?? {}) as Record<string, unknown>;
  const sample = body.sample === '1' || body.sample === 'true';
  const links = parseLinks(body.links);
  const goal = field(body.goal, 300);
  const notes = field(body.notes, 20000);

  if (!sample && !file && !usableLinks(links).length && !notes) {
    throw new HttpError(
      400,
      links.length
        ? "None of those links look like valid web addresses. Add a resume or a full URL such as github.com/you."
        : 'Add a resume, a link or some notes to analyse.',
    );
  }

  const resume: UploadedFile | undefined =
    file && !sample ? { path: file.path, originalName: file.originalname, mimetype: file.mimetype, size: file.size } : undefined;
  if (file && sample) await removeUpload(file.path);

  const stream = ndjson<AnalysisEvent>(res);
  try {
    await runAnalysis({ resume, links, goal, notes, sample }, stream.send, stream.signal);
  } catch (err) {
    if (stream.signal.aborted) {
      console.log('[analyze] cancelled by client');
    } else if (!stream.terminal) {
      console.error('[analyze]', err);
      stream.send({ type: 'error', message: humanizeError(err) });
    } else if (!(err instanceof AnalysisFailed)) {
      console.error('[analyze]', err);
    }
  } finally {
    stream.end();
    if (resume) await removeUpload(resume.path);
  }
});

const ChatBody = z.object({
  profileId: z.string().trim().min(1).max(120),
  message: z.string().trim().min(1).max(8000),
  origin: z
    .object({
      kind: z.enum(['skill', 'domain', 'source', 'role', 'requirement', 'you', 'free']),
      refId: z.string().max(200).optional(),
      title: z.string().max(200).default(''),
      caption: z.string().max(300).default(''),
    })
    .optional(),
  sessionId: z.string().max(200).optional(),
});

app.post('/api/chat', express.json({ limit: '1mb' }), async (req, res) => {
  const parsed = ChatBody.safeParse(req.body);
  if (!parsed.success) {
    const first = parsed.error.issues[0];
    throw new HttpError(400, first ? `Invalid chat request: ${first.path.join('.') || 'body'} ${first.message}` : 'Invalid chat request');
  }
  const stream = ndjson<ChatEvent>(res);
  try {
    await runChat(parsed.data, stream.send, stream.signal);
  } catch (err) {
    if (!stream.signal.aborted && !stream.terminal) {
      console.error('[chat]', err);
      stream.send({ type: 'error', message: humanizeError(err) });
    }
  } finally {
    stream.end();
  }
});

app.get('/api/profile/:id', async (req, res) => {
  const profile = await getProfile(req.params.id);
  if (!profile) throw new HttpError(404, 'Profile not found');
  res.json(profile);
});

app.get('/api/sample-profile', (_req, res) => {
  if (!hasSampleProfile()) throw new HttpError(404, 'No pre-computed sample profile yet. Run "npm run make-sample" on the server.');
  res.setHeader('Cache-Control', 'no-cache');
  res.sendFile(SAMPLE_PROFILE_PATH);
});

app.use('/api', (_req, _res) => {
  throw new HttpError(404, 'Not found');
});

/* built web app, if any */
if (existsSync(join(DIST, 'index.html'))) {
  app.use(express.static(DIST, { index: false, maxAge: '1h' }));
  app.get('/{*splat}', (_req, res) => {
    res.setHeader('Cache-Control', 'no-cache');
    res.sendFile(join(DIST, 'index.html'));
  });
}

/* JSON errors */
app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
  const e = err as { status?: unknown; statusCode?: unknown; type?: unknown; message?: unknown };
  const status =
    err instanceof HttpError ? err.status : typeof e.status === 'number' ? e.status : typeof e.statusCode === 'number' ? e.statusCode : 500;
  let message: string;
  if (err instanceof HttpError) message = err.message;
  else if (e.type === 'entity.parse.failed') message = 'Invalid JSON body';
  else if (e.type === 'entity.too.large') message = 'Request body too large';
  else if (status < 500 && typeof e.message === 'string') message = e.message;
  else message = 'Something went wrong on the server.';
  if (status >= 500) console.error('[error]', err);
  if (res.headersSent) {
    res.end();
    return;
  }
  res.status(status).json({ error: message });
});

const server = app.listen(PORT, () => {
  console.log(`Tayyari API on http://localhost:${PORT}  (reader ${MODELS.reader} · synth ${MODELS.synth} · chat ${MODELS.chat})`);
  if (existsSync(join(DIST, 'index.html'))) console.log(`Serving the web app from ${DIST}`);
});
server.on('error', (err: NodeJS.ErrnoException) => {
  if (err.code === 'EADDRINUSE') console.error(`Port ${PORT} is already in use. Set TAYYARI_API_PORT to use another port.`);
  else console.error('[server]', err);
  process.exit(1);
});
