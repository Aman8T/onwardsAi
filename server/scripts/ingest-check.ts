/**
 * Live check for deterministic ingestion (no AI, no credentials needed).
 *   npx tsx server/scripts/ingest-check.ts
 *   npx tsx server/scripts/ingest-check.ts --only classify,github
 *   npx tsx server/scripts/ingest-check.ts --url https://some.site/page
 * Sections: classify, terms, resume (includes notes), github, github-repo (opt-in), web, errors
 *
 * GitHub allows 60 unauthenticated API calls per hour and one profile costs about 12,
 * so set GITHUB_TOKEN (any token, no scopes needed) if you run this repeatedly.
 * Set INGEST_DEBUG=1 to see the underlying error when a web fetch fails.
 */
import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  classifyUrl,
  findTerms,
  ingestGithub,
  ingestNotes,
  ingestResume,
  ingestWeb,
  TERM_COUNT,
  type RawSource,
  type UploadedFile,
} from '../ingest/index.ts';

/* ───────────────────────── args ───────────────────────── */

const args = process.argv.slice(2);
const flag = (name: string): string | undefined => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
};
const only = flag('--only')?.split(',').map((s) => s.trim());
const customUrl = flag('--url');
const run = (section: string): boolean => (customUrl ? false : !only || only.includes(section));

/* ───────────────────────── output helpers ───────────────────────── */

const t0 = Date.now();
const stamp = (): string => `${((Date.now() - t0) / 1000).toFixed(1)}s`;
const rule = (title: string): void => console.log(`\n${'='.repeat(78)}\n${title}\n${'='.repeat(78)}`);
const logger = (who: string) => (text: string): void => console.log(`  [${stamp()}] ${who} > ${text}`);

function show(title: string, src: RawSource): void {
  console.log(`\n--- ${title}`);
  console.log(`id=${src.id} kind=${src.kind} label="${src.label}" kindLabel="${src.kindLabel}" src="${src.src}"`);
  console.log(`error:        ${src.error ?? '(none)'}`);
  console.log(`content:      ${src.content.length} chars${src.filePath ? `  filePath=${src.filePath}` : ''}`);
  console.log(`quickSignals: ${JSON.stringify(src.quickSignals)}`);
  console.log(`facts:        ${JSON.stringify(src.facts)}`);
  console.log('first 400 chars:');
  console.log(
    src.content
      .slice(0, 400)
      .split('\n')
      .map((l) => '  | ' + l)
      .join('\n'),
  );
}

/* ───────────────────────── fixtures: a text resume, a PDF and a DOCX built by hand ───────────────────────── */

const RESUME_LINES = [
  'Aanya Verma',
  'Frontend Engineer | Bengaluru, India | aanya@example.com',
  '',
  'SUMMARY',
  'Frontend engineer with 3 years of experience building React and TypeScript products, now moving into AI-powered apps.',
  '',
  'EXPERIENCE',
  'Software Engineer, Orbit Labs (2023 - Present)',
  '- Built a design system in React, TypeScript and Storybook used by 5 product teams',
  '- Led the migration from Webpack to Vite, cutting CI build time by 40 percent',
  '- Mentored 2 junior engineers and ran weekly code reviews',
  '- Integrated an LLM-based search feature using OpenAI and RAG with pgvector',
  'Frontend Intern, Pixel Forge (2022)',
  '- Shipped accessible UI components with Tailwind CSS and Jest tests',
  '',
  'PROJECTS',
  'RecipeGenie - AI recipe planner built with React, Supabase and Stripe, deployed on Vercel',
  'MotionKit - open source animation library for React using Framer Motion',
  '',
  'SKILLS',
  'TypeScript, JavaScript, React, Next.js, Node.js, Python, PostgreSQL, Docker, Figma, GitHub Actions, Go',
  '',
  'EDUCATION',
  'B.Tech in Computer Science, 2022',
];

function makePdf(input: string[]): Buffer {
  const esc = (s: string): string => s.replace(/[\\()]/g, '\\$&');
  // Wrap long lines so nothing runs off the page.
  const lines = input.flatMap((l) => l.match(/.{1,85}(?:\s|$)|\S+/g)?.map((s) => s.trim()) ?? ['']);
  const stream = lines.length ? `BT /F1 11 Tf 50 800 Td 14 TL ${lines.map((l) => `(${esc(l)}) Tj T*`).join(' ')} ET` : '';
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ];
  let body = '%PDF-1.4\n';
  const offsets: number[] = [];
  objects.forEach((o, i) => {
    offsets.push(body.length);
    body += `${i + 1} 0 obj\n${o}\nendobj\n`;
  });
  const xref = body.length;
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const off of offsets) body += `${String(off).padStart(10, '0')} 00000 n \n`;
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(body, 'latin1');
}

function crc32(buf: Buffer): number {
  let c = 0xffffffff;
  for (const byte of buf) {
    c ^= byte;
    for (let k = 0; k < 8; k++) c = c & 1 ? (c >>> 1) ^ 0xedb88320 : c >>> 1;
  }
  return (c ^ 0xffffffff) >>> 0;
}

/** A minimal stored (uncompressed) zip with just enough parts for Word-compatible readers. */
function makeDocx(paragraphs: string[]): Buffer {
  const xmlEsc = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const files: Array<[string, string]> = [
    [
      '[Content_Types].xml',
      '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
    ],
    [
      '_rels/.rels',
      '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
    ],
    [
      'word/document.xml',
      `<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>${paragraphs
        .map((p) => `<w:p><w:r><w:t xml:space="preserve">${xmlEsc(p)}</w:t></w:r></w:p>`)
        .join('')}</w:body></w:document>`,
    ],
  ];
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;
  for (const [name, text] of files) {
    const nameBuf = Buffer.from(name);
    const data = Buffer.from(text);
    const crc = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBuf.length, 26);
    locals.push(local, nameBuf, data);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(nameBuf.length, 28);
    central.writeUInt32LE(offset, 42);
    centrals.push(central, nameBuf);
    offset += local.length + nameBuf.length + data.length;
  }
  const centralBuf = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(centralBuf.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, centralBuf, end]);
}

async function upload(dir: string, name: string, data: Buffer | string, mimetype: string, saveAs?: string): Promise<UploadedFile> {
  const file = path.join(dir, saveAs ?? name);
  await writeFile(file, data);
  const size = typeof data === 'string' ? Buffer.byteLength(data) : data.length;
  return { path: file, originalName: name, mimetype, size };
}

/* ───────────────────────── 1. classifyUrl ───────────────────────── */

if (run('classify')) {
  rule('classifyUrl');
  const samples = [
    'github.com/gaearon',
    'https://github.com/vercel/next.js',
    'https://recipe-genie.lovable.app/',
    'https://lovable.dev/projects/3fa73859-dba9-4c0d-b91f-883d00b23682',
    'www.linkedin.com/in/some-person/',
    'https://dribbble.com/someone',
    'https://someone.github.io/site',
    'https://my-app.vercel.app',
    'https://janedoe.com/portfolio',
    'https://www.kaggle.com/someone',
    'https://leetcode.com/u/someone/',
    'https://medium.com/@someone',
    'https://someone.substack.com',
    'https://stackoverflow.com/users/1/someone',
    'https://huggingface.co/someone',
    'https://overreacted.io',
    'https://example.co.uk/about',
    'mailto:me@example.com',
    'ftp://example.com/file',
    'not a url',
    'hello',
    '',
  ];
  for (const s of samples) {
    const r = classifyUrl(s);
    console.log(`${JSON.stringify(s).padEnd(72)} -> ${r ? `${r.kind.padEnd(9)} "${r.label}" / "${r.kindLabel}"  ${r.url}` : 'null'}`);
  }
}

/* ───────────────────────── 2. term dictionary ───────────────────────── */

if (run('terms')) {
  rule(`terms (${TERM_COUNT} dictionary entries)`);
  const sample =
    'Built a RAG pipeline with LangChain and pgvector. Led a team of 4 and mentored juniors. ' +
    'Stack: React, React Native, TypeScript, Node.js, PostgreSQL, C++, C#, Go, Rust, SQL, NoSQL. Not Java but JavaScript. ' +
    'Let us go to market. Used CI/CD, A/B testing and Figma. Kubernetes (k8s).';
  console.log(findTerms(sample, 40).join(', '));
  console.log('Resume sample top 8:', findTerms(RESUME_LINES.join('\n'), 8).join(', '));
}

/* ───────────────────────── 3. resume + notes ───────────────────────── */

if (run('resume')) {
  rule('ingestResume + ingestNotes');
  const dir = await mkdtemp(path.join(tmpdir(), 'tayyari-ingest-'));
  const resumeText = RESUME_LINES.join('\n');

  show('TXT resume', await ingestResume(await upload(dir, 'aanya-resume.txt', resumeText, 'text/plain'), logger('txt')));
  show('PDF resume (upload saved without extension, like multer)', await ingestResume(await upload(dir, 'Aanya_Verma_Resume.pdf', makePdf(RESUME_LINES), 'application/pdf', 'a1b2c3d4'), logger('pdf')));
  show('DOCX resume', await ingestResume(await upload(dir, 'Aanya Verma CV.docx', makeDocx(RESUME_LINES), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'), logger('docx')));
  show('Scanned-style PDF (no text layer)', await ingestResume(await upload(dir, 'scan.pdf', makePdf([]), 'application/pdf', 'e5f6a7b8'), logger('scan')));
  show('Corrupt PDF', await ingestResume(await upload(dir, 'broken.pdf', 'this is not a pdf at all', 'application/pdf'), logger('corrupt')));
  show('Unsupported type', await ingestResume(await upload(dir, 'photo.png', Buffer.from([0x89, 0x50, 0x4e, 0x47, 0, 0, 0, 0]), 'image/png'), logger('png')));
  show('Legacy .doc', await ingestResume(await upload(dir, 'old.doc', Buffer.from('legacy'), 'application/msword'), logger('doc')));
  show('Notes', ingestNotes('  I want to move from frontend into AI product engineering.\n\n\nLed a team of 4 at Orbit Labs; strong in React, Python and Figma.  '));
}

/* ───────────────────────── 4. GitHub ───────────────────────── */

if (run('github')) {
  rule('ingestGithub("https://github.com/gaearon")');
  const gh = await ingestGithub('https://github.com/gaearon', logger('github'));
  show('GitHub profile', gh);
  console.log('\nfull content length', gh.content.length, '-- sections:');
  console.log(gh.content.split('\n').filter((l) => l.startsWith('## ')).join('\n'));
  // Opt-in: costs another ~13 of the 60 unauthenticated GitHub API calls per hour.
  if (only?.includes('github-repo')) {
    const repo = await ingestGithub('https://github.com/gaearon/overreacted.io', logger('github-repo'));
    show('GitHub repo URL (/owner/repo)', repo);
  }
}

/* ───────────────────────── 5. web pages ───────────────────────── */

async function web(url: string, title: string): Promise<void> {
  const c = classifyUrl(url);
  if (!c) {
    console.log(`\n--- ${title}\nclassifyUrl returned null for ${url}`);
    return;
  }
  show(`${title} [${c.kind}]`, await ingestWeb(c.url, c.kind, c.label, c.kindLabel, logger(c.kind)));
}

if (customUrl) {
  rule(`custom url: ${customUrl}`);
  await web(customUrl, 'Custom URL');
}

if (run('web')) {
  rule('ingestWeb');
  await web('https://overreacted.io', 'Portfolio / blog (static HTML)');
  await web('https://tourflow.lovable.app', 'Lovable app (Vite + React SPA)');
  await web('https://tour-flow-mu.vercel.app', 'Lovable-built SPA on Vercel');
}

/* ───────────────────────── 6. expected failures must not throw ───────────────────────── */

if (run('errors')) {
  rule('expected failures (must return error, never throw)');
  await web('https://www.linkedin.com/in/williamhgates/', 'LinkedIn');
  await web('https://lovable.dev/projects/3fa73859-dba9-4c0d-b91f-883d00b23682', 'Lovable editor link');
  await web('https://overreacted.io/this-page-does-not-exist-12345/', '404 page');
  await web('https://this-domain-should-not-exist-tayyari-12345.com', 'Unknown domain');
  await web('http://127.0.0.1:9/', 'Private address');
  show('Bad GitHub user', await ingestGithub('https://github.com/tayyari-no-such-user-98765', logger('github')));
  show('Not a GitHub link', await ingestGithub('https://example.com/foo', logger('github')));
}

console.log(`\nDone in ${stamp()}`);
