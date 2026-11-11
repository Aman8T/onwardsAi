/**
 * Fixtures for the resume gallery (`/dev/resume`): one invented person's blocks, the resume she uploaded, a resume
 * built for a next role with a few changes, the two answers Finalise can give, and the blocks view. Everything is
 * typed against `shared/resume.ts`, so a change to the contract fails the typecheck here first.
 *
 * `applyOp` stands in for the server's `apply_ops` and `marks` so the gallery's buttons really move, leave out, put
 * back, edit and reset. It is a sketch for looking at the page, not the rule: the backend owns that.
 */
import type {
  BlocksResponse,
  Bullet,
  BulletMark,
  ExperienceEntry,
  Fact,
  FinaliseReport,
  GrammarProposal,
  LineWording,
  LintReport,
  MergeEntry,
  MergeReport,
  ResumeDetail,
  ResumeFile,
  ResumeList,
  ResumeOp,
  ResumeSummary,
  ResumeVariant,
} from '../types';

/* ───────────────────────── the blocks ───────────────────────── */

const fromFile = (id: string, line: number, text: string, kind: Fact['kind'] = 'accomplishment'): Fact => ({
  id,
  text,
  kind,
  refs: [{ source: 'resume', docId: 'resume', lines: [line, line], quote: text }],
  evidenceTier: 'used',
});

const E1 = fromFile('e1', 5, 'Frontend Engineer, Lumen Pay, 2024 - Present', 'experience');
const E2 = fromFile('e2', 11, 'Frontend Developer, Tessel Labs, 2023 - 2024', 'experience');
const E3 = fromFile('e3', 15, 'Junior Developer, Pixelcraft Studio, 2022 - 2023', 'experience');
const P1 = fromFile('p1', 19, 'motion-kit', 'project');
const P2 = fromFile('p2', 21, 'notes-ai', 'project');
const ED1 = fromFile('ed1', 26, 'B.Tech, Computer Science, 2022', 'education');

const BULLET_FACTS: Fact[] = [
  fromFile('f6', 6, 'Own the component library every product team builds on.'),
  fromFile('f7', 7, 'Led the move from CSS modules to design tokens.'),
  fromFile('f8', 8, 'Mentored interns through their first production release.'),
  fromFile('f9', 9, 'Built Kite UI: 42 components, documented in Storybook.'),
  fromFile('f10', 10, 'Cut bundle size 31% with route-level code splitting.'),
  fromFile('f12', 12, 'Rebuilt the checkout in React and TypeScript.'),
  fromFile('f13', 13, 'Wrote the motion guidelines the design team still uses.'),
  fromFile('f14', 14, 'Checkout LCP 4.1s to 1.9s, conversion +9.4%.'),
  fromFile('f16', 16, 'Built marketing sites for 9 clients on Next.js.'),
  fromFile('f17', 17, "Shipped a client's storefront redesign in 3 weeks."),
  fromFile('f20', 20, 'Spring-physics primitives for React, open source.'),
  fromFile('f22', 22, 'A notes app with an LLM summary, built on Lovable.'),
];

const GITHUB: Fact = {
  id: 'g1',
  text: 'motion-kit has 212 stars and 14 contributors.',
  kind: 'project',
  refs: [{ source: 'github', docId: 'github.com/riyak/motion-kit', quote: '212 stars · 14 contributors' }],
  evidenceTier: 'artifact',
};

const SKILL_FACTS: Fact[] = [
  { ...fromFile('s1', 24, 'React, TypeScript, Next.js, CSS, Framer Motion, Storybook, Figma', 'skill'), evidenceTier: 'listed' },
  { ...fromFile('s2', 25, 'Node, PostgreSQL, REST', 'skill'), evidenceTier: 'listed' },
];

/** The line she typed and kept as her own words at Finalise. */
const OWN: Fact = {
  id: 'u1',
  text: 'Led the move from CSS modules to design tokens across 6 product teams.',
  kind: 'accomplishment',
  refs: [{ source: 'user', docId: 'resume-rs_path_de', quote: 'Led the move from CSS modules to design tokens across 6 product teams.' }],
};

const BOOK: Fact[] = [E1, E2, E3, P1, P2, ED1, ...BULLET_FACTS, GITHUB, ...SKILL_FACTS];
const factOf = (id: string): Fact => {
  const fact = [...BOOK, OWN].find((f) => f.id === id);
  if (!fact) throw new Error(`fixture: no fact ${id}`);
  return fact;
};

/** Which job or project a bullet block sits under. */
const ENTRY_OF: Record<string, string> = {
  f6: 'e1',
  f7: 'e1',
  f8: 'e1',
  f9: 'e1',
  f10: 'e1',
  f12: 'e2',
  f13: 'e2',
  f14: 'e2',
  f16: 'e3',
  f17: 'e3',
  f20: 'p1',
  g1: 'p1',
  f22: 'p2',
  u1: 'e1',
};

/* ───────────────────────── the variants ───────────────────────── */

const bullet = (factId: string, patch: Partial<Bullet> = {}): Bullet => ({
  id: `b-${factId}`,
  text: factOf(factId).text,
  factIds: [factId],
  verdict: 'exact',
  placeholders: [],
  status: 'verified',
  ...patch,
});

const entry = (entryFactId: string, title: string, org: string | undefined, dates: string | undefined, factIds: string[]): ExperienceEntry => ({
  entryFactId,
  title,
  ...(org ? { org } : {}),
  ...(dates ? { dates } : {}),
  bullets: factIds.map((id) => bullet(id)),
});

const shell = (experience: ExperienceEntry[], projects: ExperienceEntry[]): ResumeVariant => ({
  header: { name: 'Riya Kapoor', headline: 'Frontend Engineer · Bengaluru', contact: ['riya@example.com', 'riyakapoor.design', 'github.com/riyak'] },
  summary: { id: 'summary', text: '', factIds: [], verdict: 'exact', placeholders: [], status: 'dropped' },
  skills: [
    {
      group: 'Frontend',
      items: ['React', 'TypeScript', 'Next.js', 'CSS', 'Framer Motion', 'Storybook', 'Figma'].map((name) => ({ name, factIds: ['s1'], evidenceTier: 'listed' as const })),
    },
    { group: 'Backend', items: ['Node', 'PostgreSQL', 'REST'].map((name) => ({ name, factIds: ['s2'], evidenceTier: 'listed' as const })) },
  ],
  experience,
  projects,
  education: [{ factId: 'ed1', title: 'B.Tech, Computer Science', dates: '2022' }],
  decisions: [],
  changes: [],
});

const UPLOAD_VARIANT = shell(
  [
    entry('e1', 'Frontend Engineer', 'Lumen Pay', '2024 - Present', ['f6', 'f7', 'f8', 'f9', 'f10']),
    entry('e2', 'Frontend Developer', 'Tessel Labs', '2023 - 2024', ['f12', 'f13', 'f14']),
    entry('e3', 'Junior Developer', 'Pixelcraft Studio', '2022 - 2023', ['f16', 'f17']),
  ],
  [entry('p1', 'motion-kit', undefined, undefined, ['f20']), entry('p2', 'notes-ai', undefined, undefined, ['f22'])],
);

const TYPED = 'Led the move from CSS modules to design tokens across 6 product teams.';

const PATH_VARIANT: ResumeVariant = (() => {
  const v = shell(
    [
      entry('e1', 'Frontend Engineer', 'Lumen Pay', '2024 - Present', ['f9', 'f7', 'f6', 'f10', 'f8']),
      entry('e2', 'Frontend Developer', 'Tessel Labs', '2023 - 2024', ['f13', 'f14', 'f12']),
      entry('e3', 'Junior Developer', 'Pixelcraft Studio', '2022 - 2023', ['f16']),
    ],
    [entry('p1', 'motion-kit', undefined, undefined, ['f20', 'g1'])],
  );
  // one line typed by the person in this draft: it waits for Finalise
  v.experience[0].bullets[1] = bullet('f7', { text: TYPED, status: 'needs_user' });
  return v;
})();

/* ───────────────────────── marks, as the server would count them ───────────────────────── */

const entriesOf = (v: ResumeVariant) => [...v.experience, ...v.projects];
const live = (e: ExperienceEntry) => e.bullets.filter((b) => b.status !== 'dropped');

/** How a draft differs from the uploaded resume: per bullet `moved`, `edited`, `added`; and the bullets it leaves out. */
function compare(variant: ResumeVariant, kind: ResumeSummary['kind']): { marks: BulletMark[]; leftOut: Fact[] } {
  const marks: BulletMark[] = [];
  const used = new Set<string>();
  for (const e of entriesOf(variant)) {
    const original = entriesOf(UPLOAD_VARIANT).find((x) => x.entryFactId === e.entryFactId);
    const was = (original ? live(original) : []).map((b) => b.factIds[0]);
    // order is compared among the bullets both resumes have, so a bullet left out does not make its neighbours "moved"
    const kept = live(e).map((b) => b.factIds[0]).filter((id) => was.includes(id));
    const wasKept = was.filter((id) => kept.includes(id));
    for (const b of live(e)) {
      const factId = b.factIds[0];
      b.factIds.forEach((id) => used.add(id));
      if (!was.includes(factId)) marks.push({ bulletId: b.id, kind: 'added' });
      else if (kept.indexOf(factId) < wasKept.indexOf(factId)) marks.push({ bulletId: b.id, kind: 'moved' });
      if (b.status === 'needs_user' || b.text !== factOf(factId).text) marks.push({ bulletId: b.id, kind: 'edited' });
    }
  }
  // the uploaded resume is the file as written: what GitHub shows is offered to the resumes built for a role
  const pool = kind === 'upload' ? BULLET_FACTS : [...BULLET_FACTS, GITHUB];
  return { marks, leftOut: pool.filter((f) => !used.has(f.id)) };
}

function describe(summary: ResumeSummary, variant: ResumeVariant, extraFacts: Fact[] = []): ResumeDetail {
  const { marks, leftOut } = compare(variant, summary.kind);
  const missingFromUpload = leftOut.filter((f) => entriesOf(UPLOAD_VARIANT).some((e) => e.bullets.some((b) => b.factIds.includes(f.id)))).length;
  const cited = new Set([...entriesOf(variant).flatMap((e) => [e.entryFactId, ...e.bullets.flatMap((b) => b.factIds)]), 's1', 's2', 'ed1']);
  return {
    resume: { ...summary, changes: marks.length + missingFromUpload },
    variant,
    marks,
    facts: [...BOOK, ...extraFacts].filter((f) => cited.has(f.id)),
    leftOut,
    canDownload: summary.finalisedAt !== undefined,
  };
}

/* ───────────────────────── the resumes ───────────────────────── */

const UPLOAD_SUMMARY: ResumeSummary = {
  id: 'rs_upload',
  kind: 'upload',
  title: 'riya-kapoor-2026.pdf',
  state: 'final',
  isDefault: true,
  changes: 0,
  updatedAt: '2026-11-02T09:12:00Z',
  finalisedAt: '2026-11-02T09:12:00Z',
};

const PATH_SUMMARY: ResumeSummary = {
  id: 'rs_path_de',
  kind: 'path',
  title: 'Design Engineer',
  roleId: 'design-engineer',
  state: 'draft',
  isDefault: false,
  changes: 0,
  updatedAt: '2026-11-10T08:40:00Z',
};

const DSE_SUMMARY: ResumeSummary = {
  id: 'rs_path_dse',
  kind: 'path',
  title: 'Design Systems Engineer',
  roleId: 'design-systems-engineer',
  state: 'final',
  isDefault: false,
  changes: 5,
  updatedAt: '2026-11-06T17:05:00Z',
  finalisedAt: '2026-11-06T17:05:00Z',
};

/** The uploaded resume: final, the default, every bullet in the order of the file. */
export const UPLOAD_DETAIL: ResumeDetail = describe(UPLOAD_SUMMARY, UPLOAD_VARIANT);

/** A draft built for a next role: three bullets moved up, one typed over, one added from GitHub, two left out. */
export const PATH_DETAIL: ResumeDetail = describe(PATH_SUMMARY, PATH_VARIANT);

/** The bullet pressed when the gallery opens the draft. */
export const PRESSED_BULLET = 'b-f7';

export const LIST: ResumeList = {
  profileId: 'pf_riya',
  resumes: [UPLOAD_DETAIL.resume, PATH_DETAIL.resume, DSE_SUMMARY],
  paths: [
    { roleId: 'design-engineer', title: 'Design Engineer', alignment: 86, resumeId: 'rs_path_de' },
    { roleId: 'design-systems-engineer', title: 'Design Systems Engineer', alignment: 80, resumeId: 'rs_path_dse' },
    { roleId: 'senior-frontend-engineer', title: 'Senior Frontend Engineer', alignment: 78 },
    { roleId: 'full-stack-product-engineer', title: 'Full-Stack Product Engineer', alignment: 61 },
    { roleId: 'ai-product-engineer', title: 'AI Product Engineer', alignment: 44 },
  ],
  blocks: BOOK.length,
};

/* ───────────────────────── finalise ───────────────────────── */

/** The checks found a figure no block has: one finding, two answers. */
export const REPORT_FINDING: FinaliseReport = {
  done: false,
  findings: [{ bulletId: 'b-f7', kind: 'number', text: TYPED, what: '6 product teams', options: ['mine', 'undo'] }],
  fixed: [],
};

const LINT: LintReport = {
  items: [
    { id: 'cites_fact', ok: true, label: 'Every sentence cites a fact', detail: 'Every sentence cites facts, and the files hold nothing else.' },
    { id: 'numbers_cited', ok: true, label: 'Every number comes from a cited fact', detail: 'Every number is in a fact the resume cites.' },
    { id: 'text_layer', ok: true, label: 'The file has a real text layer', detail: 'Both files carry real text.' },
    { id: 'single_column', ok: true, label: 'One column, no tables or text boxes', detail: 'One column of plain paragraphs.' },
    { id: 'standard_headings', ok: true, label: 'Standard section headings', detail: 'Headings: EXPERIENCE, PROJECTS, SKILLS, EDUCATION.' },
    { id: 'contact_text', ok: true, label: 'Contact details are plain text', detail: 'Email and links are plain text in the body.' },
    { id: 'date_ranges', ok: true, label: 'Dates are plain ranges with both ends', detail: 'Every date range reads like 2024 - Present.' },
    { id: 'page_count', ok: true, label: 'Page count', detail: '1 page' },
    { id: 'round_trip', ok: true, label: 'The text survives extraction', detail: 'All 13 sentences read back exactly.' },
  ],
};

/** The same report with one item still open, to see how an open check reads. */
export const LINT_ONE_OPEN: LintReport = {
  items: LINT.items.map((item) => (item.id === 'page_count' ? { ...item, ok: false, detail: '3 pages. Two or fewer read best.', location: 'page 3' } : item)),
};

export const REPORT_DONE: FinaliseReport = {
  done: true,
  findings: [],
  fixed: [
    'Date ranges are written one way: "2024 - Present". Applicant systems read that form.',
    'Plain headings in one column: EXPERIENCE, PROJECTS, SKILLS, EDUCATION.',
    'The contact line is ordinary text in the body, not in a header or footer.',
  ],
  lint: LINT,
};

/** The draft once it is finalised with "It is true. Keep it as my words": the typed line now rests on her own fact. */
export const DONE_DETAIL: ResumeDetail = (() => {
  const variant: ResumeVariant = JSON.parse(JSON.stringify(PATH_VARIANT)) as ResumeVariant;
  variant.experience[0].bullets[1] = bullet('f7', { text: TYPED, factIds: ['f7', 'u1'], status: 'verified' });
  variant.lint = LINT;
  const detail = describe({ ...PATH_SUMMARY, state: 'final', finalisedAt: '2026-11-10T09:02:00Z', updatedAt: '2026-11-10T09:02:00Z' }, variant, [OWN]);
  return detail;
})();

/* ───────────────────────── the blocks view ───────────────────────── */

const RESUMES = 3;
const USED_IN: Record<string, number> = { f17: 2, f22: 2, g1: 1, f8: 2, u1: 1 };

export const BLOCKS: BlocksResponse = {
  profileId: 'pf_riya',
  entries: [
    { factId: 'e1', kind: 'experience', title: 'Frontend Engineer', org: 'Lumen Pay', dates: '2024 - Present' },
    { factId: 'e2', kind: 'experience', title: 'Frontend Developer', org: 'Tessel Labs', dates: '2023 - 2024' },
    { factId: 'e3', kind: 'experience', title: 'Junior Developer', org: 'Pixelcraft Studio', dates: '2022 - 2023' },
    { factId: 'p1', kind: 'project', title: 'motion-kit' },
    { factId: 'p2', kind: 'project', title: 'notes-ai' },
    { factId: 'ed1', kind: 'education', title: 'B.Tech, Computer Science', dates: '2022' },
  ],
  blocks: [...BOOK, OWN].map((fact) => ({
    fact,
    ...(ENTRY_OF[fact.id] ? { entryFactId: ENTRY_OF[fact.id] } : {}),
    usedIn: USED_IN[fact.id] ?? RESUMES,
  })),
  resumes: RESUMES,
};

/* ───────────────────────── a stand-in for the server's operations ───────────────────────── */

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

/** One operation applied the way the server would, or a sentence saying why it does not fit. */
export function applyOp(detail: ResumeDetail, op: ResumeOp): ResumeDetail | string {
  const variant = clone(detail.variant);
  const entries = entriesOf(variant);
  const home = op.bulletId ? entries.find((e) => e.bullets.some((b) => b.id === op.bulletId)) : undefined;
  const at = home ? home.bullets.findIndex((b) => b.id === op.bulletId) : -1;

  if (op.op === 'put_back') {
    const fact = detail.leftOut.find((f) => f.id === op.factId);
    const target = entries.find((e) => e.entryFactId === ENTRY_OF[op.factId ?? '']);
    if (!fact) return 'That block is already in this resume.';
    if (target) target.bullets.push(bullet(fact.id));
    else variant.projects.push(entry(ENTRY_OF[fact.id] ?? fact.id, factOf(ENTRY_OF[fact.id] ?? fact.id).text, undefined, undefined, [fact.id]));
  } else if (!home || at < 0) {
    return 'That line is not in this resume.';
  } else if (op.op === 'move') {
    const to = op.dir === 'up' ? at - 1 : at + 1;
    if (to < 0 || to >= home.bullets.length) return op.dir === 'up' ? 'That line is already the first under its job.' : 'That line is already the last under its job.';
    [home.bullets[at], home.bullets[to]] = [home.bullets[to], home.bullets[at]];
  } else if (op.op === 'leave_out') {
    home.bullets.splice(at, 1);
  } else if (op.op === 'edit') {
    const text = (op.text ?? '').trim();
    if (!text) return 'A line needs some words.';
    home.bullets[at] = { ...home.bullets[at], text, status: 'needs_user' };
  } else {
    const first = home.bullets[at].factIds[0];
    home.bullets[at] = bullet(first, { id: home.bullets[at].id });
  }

  const next = describe({ ...detail.resume, state: 'draft', updatedAt: '2026-11-10T09:30:00Z' }, variant);
  return { ...next, canDownload: detail.canDownload };
}

/* ───────────────────────── version 2: grammar, wordings elsewhere, a posting resume ───────────────────────── */

/** The draft with two lines the person typed, each with a slip in it: what the grammar check is shown against. */
export const GRAMMAR_DETAIL: ResumeDetail = (() => {
  let detail: ResumeDetail = PATH_DETAIL;
  const edits: [string, string][] = [
    ['b-f10', 'Cut bundle size 31% with route-level code splitting accross the app.'],
    ['b-f8', 'Mentored interns through there first production release.'],
  ];
  for (const [bulletId, text] of edits) {
    const next = applyOp(detail, { op: 'edit', bulletId, text });
    if (typeof next !== 'string') detail = next;
  }
  return detail;
})();

export const GRAMMAR_PROPOSALS: GrammarProposal[] = [
  { bulletId: 'b-f10', before: 'Cut bundle size 31% with route-level code splitting accross the app.', after: 'Cut bundle size 31% with route-level code splitting across the app.', why: 'Spelling: "across" has one c.' },
  { bulletId: 'b-f8', before: 'Mentored interns through there first production release.', after: 'Mentored interns through their first production release.', why: 'Grammar: "their" is the one that belongs to the interns.' },
];

/** What the other resumes use for lines of the draft: finalised wordings, newest first. */
export const WORDINGS: LineWording[] = [
  { lineId: 'b-f10', text: 'Cut bundle size by 31% with route-level code splitting.', fromResumeId: 'rs_path_dse', fromTitle: 'Design Systems Engineer' },
  { lineId: 'b-f10', text: 'Reduced the JavaScript shipped on first load by 31%, using route-level code splitting.', fromResumeId: 'rs_upload', fromTitle: 'riya-kapoor-2026.pdf' },
  { lineId: 'b-f6', text: 'Own the component library that every product team builds on.', fromResumeId: 'rs_path_dse', fromTitle: 'Design Systems Engineer' },
];

/** A resume she saved from a match in Match: it keeps its link back. */
const POSTING_SUMMARY: ResumeSummary = {
  id: 'rs_posting_headout',
  kind: 'posting',
  title: 'Design Engineer at Headout',
  matchId: 'm_headout',
  state: 'final',
  isDefault: false,
  changes: 4,
  updatedAt: '2026-11-01T10:20:00Z',
  finalisedAt: '2026-11-01T10:20:00Z',
};

export const LIST_WITH_POSTING: ResumeList = { ...LIST, resumes: [...LIST.resumes, POSTING_SUMMARY] };

/** The coach's second wording for the checkout line (it adds only what she said in the conversation). */
export const REWRITE_FIXTURE = {
  resumeId: 'rs_path_de',
  lineId: 'b-f14',
  before: 'Checkout LCP 4.1s to 1.9s, conversion +9.4%.',
  after: 'Cut checkout LCP from 4.1s to 1.9s (Lighthouse, mobile, median of five runs). Conversion rose 9.4%, four weeks before against four weeks after.',
};

/* ───────────────────────── a second file ───────────────────────── */

/** The 2025 file she adds: kept as its own resume. */
const UPLOAD_2025: ResumeSummary = {
  id: 'rs_upload_2025',
  kind: 'upload',
  title: 'riya-kapoor-2025.pdf',
  state: 'final',
  isDefault: false,
  changes: 0,
  updatedAt: '2026-11-10T09:30:00Z',
  finalisedAt: '2026-11-10T09:30:00Z',
};

/** How the 2025 file merged: 12 lines the same, 3 new, 2 worded differently, 2 that disagree. */
export const MERGE_REPORT: MergeReport = {
  fileId: 'file_2025',
  filename: 'riya-kapoor-2025.pdf',
  same: [
    'Frontend Engineer, Lumen Pay, 2024 - Present',
    'Own the component library every product team builds on.',
    'Led the move from CSS modules to design tokens.',
    'Mentored interns through their first production release.',
    'Frontend Developer, Tessel Labs, 2023 - 2024',
    'Rebuilt the checkout in React and TypeScript.',
    'Wrote the motion guidelines the design team still uses.',
    'Junior Developer, Pixelcraft Studio, 2022 - 2023',
    'Built marketing sites for 9 clients on Next.js.',
    "Shipped a client's storefront redesign in 3 weeks.",
    'B.Tech, Computer Science, 2022',
    'React, TypeScript, Next.js, CSS, Framer Motion, Storybook, Figma',
  ].map((text, i) => ({ blockId: `s-${i}`, kind: i % 4 === 0 ? ('experience' as const) : ('accomplishment' as const), text })),
  new: [
    { blockId: 'm-3f9a1c0b27', kind: 'accomplishment', text: 'Ran the checkout accessibility audit: 23 issues fixed.' },
    { blockId: 'm-8d20e45a11', kind: 'accomplishment', text: 'Spoke at a React Bengaluru meetup on design tokens.' },
    { blockId: 'm-c71b6e9d05', kind: 'accomplishment', text: 'Reviewed pull requests for a team of 11 engineers.' },
  ],
  near: [
    { blockId: 'f10', kind: 'accomplishment', text: 'Cut bundle size by a third with code splitting.', existing: 'Cut bundle size 31% with route-level code splitting.' },
    { blockId: 'f20', kind: 'project', text: 'Open-source spring-physics primitives for React.', existing: 'Spring-physics primitives for React, open source.' },
  ],
  disagree: [
    { blockId: 'f9', kind: 'accomplishment', text: 'Built Kite UI: 38 components.', existing: 'Built Kite UI: 42 components, documented in Storybook.', differs: ['figure'] },
    { blockId: 'e2', kind: 'experience', text: 'Frontend Developer, Tessel Labs, 2022 - 2023', existing: 'Frontend Developer, Tessel Labs, 2023 - 2024', differs: ['date'] },
  ],
};

export const MERGE_ENTRY: MergeEntry = { report: MERGE_REPORT, resumeId: UPLOAD_2025.id };

/** Her blocks after the 2025 file: one block with a second wording, one that two files disagree on, and a new block. */
export const BLOCKS_WITH_FILES: BlocksResponse = {
  ...BLOCKS,
  blocks: [
    ...BLOCKS.blocks.map((b) =>
      b.fact.id === 'f10'
        ? { ...b, wordings: [{ fileId: 'file_2025', filename: 'riya-kapoor-2025.pdf', text: MERGE_REPORT.near[0].text, resumeId: UPLOAD_2025.id }] }
        : b.fact.id === 'f9'
          ? {
              ...b,
              disagreements: [{ fileId: 'file_2025', filename: 'riya-kapoor-2025.pdf', text: MERGE_REPORT.disagree[0].text, differs: ['figure' as const], resumeId: UPLOAD_2025.id }],
            }
          : b,
    ),
    {
      fact: {
        id: 'm-3f9a1c0b27',
        text: MERGE_REPORT.new[0].text,
        kind: 'accomplishment',
        refs: [{ source: 'resume', docId: 'file_2025', lines: [11, 11], quote: MERGE_REPORT.new[0].text }],
        evidenceTier: 'used',
      },
      entryFactId: 'e2',
      usedIn: 1,
    },
  ],
  resumes: RESUMES + 1,
};

/** Both of her files: the one she was analysed from (no stored original: analysed before files were kept) and the 2025 file. */
export const FILES: ResumeFile[] = [
  { id: 'file_2026', filename: 'riya-kapoor-2026.pdf', mime: 'application/pdf', size: 86_400, origin: 'analysis', uploadedAt: '2026-11-02T09:12:00Z', resumeId: UPLOAD_DETAIL.resume.id },
  {
    id: 'file_2025',
    filename: 'riya-kapoor-2025.pdf',
    mime: 'application/pdf',
    size: 1_310_000,
    origin: 'upload',
    uploadedAt: '2026-11-10T09:30:00Z',
    resumeId: UPLOAD_2025.id,
    counts: { same: 12, new: 3, near: 2, disagree: 2 },
  },
];
