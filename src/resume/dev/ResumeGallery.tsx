/**
 * Dev gallery (`/dev/resume`, development builds only): the resume screens on fixtures, with no server and no
 * account. Pick a screen in the bar, or link straight to one (`?scheme=dark` for the dark scheme, `?h=120` to pin
 * the hue):
 *
 *   /dev/resume?screen=draft
 *
 *   upload            the uploaded resume: final, the default, nothing marked
 *   draft             a resume built for a next role: marks, a pressed line, two blocks left out
 *   making            the rail while a resume for a role is being made
 *   finalise          Finalise before the checks have run
 *   finalise-finding  one finding that only the person can answer
 *   finalise-done     finalised: what was fixed, the read-back checklist, the downloads
 *   finalise-open     the same with one check of the read-back still open
 *   finalise-grammar  Finalise with the grammar check's proposals ("Proposed · yours to accept")
 *   finalise-grammar-failed  the check could not run
 *   finalise-grammar-none    the check ran and found nothing
 *   wordings          a pressed line that other resumes word differently
 *   posting           the rail with a resume saved from a posting
 *   rewrite           the coach's second wording: to accept, accepted, the draft changed
 *   blocks            your blocks, grouped by job
 *   adding            "Add a resume file" while a file is read: the button, "Reading <file>…" (pick a file to try the checks)
 *   merge             "A second file arrives": Same, New, Near and Disagree for a file just added, over Your files
 *   blocks-files      blocks with other wordings and a disagreement (the flag opens the two texts), and Your files
 *   original-missing  an uploaded resume whose original was not kept: no "Download your file"
 *   upload-too-many   the file was refused: eight files are the most Leafout keeps
 *   upload-unreadable the file was refused: no text could be read in it
 *   loading · error   the two waiting states
 *
 * The buttons work against a stand-in for the server (`applyOp` in fixtures.ts) that answers after a beat; whatever
 * would leave the page or fetch a file says where it would go instead.
 */
import { useRef, useState } from 'react';
import { Brand } from '../../components/Brand';
import { BlocksView } from '../BlocksView';
import type { BlocksViewProps } from '../BlocksView';
import { ResumeHeader } from '../ResumeHeader';
import { UploadStrip } from '../UploadParts';
import { TOO_MANY, UNREADABLE, checkFile } from '../upload';
import { FinaliseView } from '../FinaliseView';
import { RewriteCard } from '../RewriteCard';
import type { RewritePhase } from '../RewriteCard';
import type { GrammarState } from '../useResumes';
import { StatePanel } from '../parts';
import { ResumePage } from '../ResumePage';
import type { ResumeQuery } from '../route';
import type { Busy, FinaliseReport, FindingAnswer, ResumeDetail, ResumeList, ResumeOp, UploadFailure } from '../types';
import { BLOCKS, BLOCKS_WITH_FILES, DONE_DETAIL, FILES, MERGE_ENTRY, GRAMMAR_DETAIL, GRAMMAR_PROPOSALS, LINT_ONE_OPEN, LIST, LIST_WITH_POSTING, REWRITE_FIXTURE, WORDINGS, PATH_DETAIL, PRESSED_BULLET, REPORT_DONE, REPORT_FINDING, UPLOAD_DETAIL, applyOp } from './fixtures';
import '../../styles/resume.css';

const SCREENS = [
  ['upload', 'Uploaded · final, default'],
  ['draft', 'Built for a role · draft'],
  ['making', 'Making a resume'],
  ['finalise', 'Finalise · before'],
  ['finalise-finding', 'Finalise · one finding'],
  ['finalise-done', 'Finalise · done'],
  ['finalise-open', 'Finalise · a check open'],
  ['finalise-grammar', 'Finalise · grammar proposals'],
  ['finalise-grammar-failed', 'Finalise · grammar could not run'],
  ['finalise-grammar-none', 'Finalise · nothing to fix'],
  ['wordings', 'Worded differently elsewhere'],
  ['posting', 'Saved from a posting'],
  ['rewrite', 'Coach · second wording'],
  ['blocks', 'Your blocks'],
  ['adding', 'Adding a file'],
  ['merge', 'A second file arrives'],
  ['blocks-files', 'Wordings · files'],
  ['original-missing', 'Original not kept'],
  ['upload-too-many', 'Too many files'],
  ['upload-unreadable', 'Unreadable file'],
  ['loading', 'Loading'],
  ['error', 'Error'],
] as const;
type Screen = (typeof SCREENS)[number][0];

const isScreen = (s: string | null): s is Screen => SCREENS.some(([id]) => id === s);
const readScreen = (): Screen => {
  const s = new URLSearchParams(window.location.search).get('screen');
  return isScreen(s) ? s : 'draft';
};

const BEAT_MS = 350;

export function ResumeGallery() {
  const [screen, setScreen] = useState<Screen>(readScreen);
  const go = (next: Screen) => {
    setScreen(next);
    const p = new URLSearchParams(window.location.search);
    p.set('screen', next);
    window.history.replaceState(null, '', `/dev/resume?${p.toString()}`);
  };

  return (
    <div className="rs-view">
      <header className="rs-header">
        <div className="rs-header-start">
          <Brand />
          <span className="kicker">Dev only · fixtures typed against shared/resume.ts</span>
        </div>
        <nav className="rs-header-end" aria-label="Screens">
          {SCREENS.map(([id, label]) => (
            <button key={id} type="button" className="rs-navbtn" aria-pressed={screen === id} onClick={() => go(id)}>
              {label}
            </button>
          ))}
        </nav>
      </header>
      <main className="rs-main" key={screen}>
        <Stage screen={screen} go={go} />
      </main>
    </div>
  );
}

function Stage({ screen, go }: { screen: Screen; go: (next: Screen) => void }) {
  switch (screen) {
    case 'upload':
    case 'draft':
    case 'making':
    case 'wordings':
    case 'posting':
    case 'original-missing':
      return <PageStage screen={screen} go={go} />;
    case 'adding':
    case 'upload-too-many':
    case 'upload-unreadable':
      return <UploadStage screen={screen} go={go} />;
    case 'merge':
    case 'blocks-files':
      return <FilesStage screen={screen} go={go} />;
    case 'rewrite':
      return <RewriteStage />;
    case 'finalise':
    case 'finalise-finding':
    case 'finalise-done':
    case 'finalise-open':
    case 'finalise-grammar':
    case 'finalise-grammar-failed':
    case 'finalise-grammar-none':
      return <FinaliseStage screen={screen} go={go} />;
    case 'blocks':
      return <BlocksView data={BLOCKS} status="ready" onRetry={() => undefined} back={{ screen: 'resume' }} onNavigate={() => go('draft')} />;
    case 'loading':
      return <StatePanel title="Opening your resumes…" busy />;
    default:
      return (
        <StatePanel
          title="Your resumes didn't load"
          action={
            <button type="button" className="btn btn-primary rs-btn" onClick={() => go('draft')}>
              Try again
            </button>
          }
        >
          Leafout is having trouble right now. Please try again in a moment.
        </StatePanel>
      );
  }
}

/* ───────────────────────── the resumes screen ───────────────────────── */

const DETAILS: Record<string, ResumeDetail> = { [UPLOAD_DETAIL.resume.id]: UPLOAD_DETAIL, [PATH_DETAIL.resume.id]: PATH_DETAIL };

function PageStage({ screen, go }: { screen: 'upload' | 'draft' | 'making' | 'wordings' | 'posting' | 'original-missing'; go: (next: Screen) => void }) {
  const [detail, setDetail] = useState<ResumeDetail>(screen === 'upload' || screen === 'original-missing' ? UPLOAD_DETAIL : PATH_DETAIL);
  const [list, setList] = useState<ResumeList>(screen === 'posting' ? LIST_WITH_POSTING : LIST);
  const [busy, setBusy] = useState<Busy | null>(screen === 'making' ? { kind: 'make', roleId: 'senior-frontend-engineer' } : null);
  const [notice, setNotice] = useState<string | undefined>();
  const latest = useRef(detail);
  latest.current = detail;

  const show = (next: ResumeDetail) => {
    setDetail(next);
    setList((l) => ({
      ...l,
      resumes: l.resumes.map((r) => (r.id === next.resume.id ? next.resume : next.resume.isDefault && r.isDefault ? { ...r, isDefault: false } : r)),
    }));
  };

  const onOp = (op: ResumeOp) =>
    new Promise<ResumeDetail | null>((resolve) => {
      setBusy({ kind: 'op', op: op.op, target: op.bulletId ?? op.factId ?? '' });
      setNotice(undefined);
      window.setTimeout(() => {
        const next = applyOp(latest.current, op);
        setBusy(null);
        if (typeof next === 'string') {
          setNotice(next);
          resolve(null);
        } else {
          show(next);
          resolve(next);
        }
      }, BEAT_MS);
    });

  const onNavigate = (to: ResumeQuery) => {
    setNotice(undefined);
    if (to.screen === 'blocks') go('blocks');
    else if (to.id && DETAILS[to.id]) setDetail(DETAILS[to.id]);
    else setNotice('Gallery: only the uploaded resume and the Design Engineer draft are stored here.');
  };

  return (
    <ResumePage
      key={detail.resume.id}
      list={list}
      detail={detail}
      detailStatus="ready"
      viewId={detail.resume.id}
      busy={busy}
      notice={notice}
      initialSelected={detail.resume.id === PATH_DETAIL.resume.id ? (screen === 'draft' ? PRESSED_BULLET : screen === 'wordings' ? 'b-f10' : undefined) : undefined}
      wordings={screen === 'wordings' ? WORDINGS : undefined}
      onOpenMatch={(matchId) => setNotice(`Gallery: this opens the match at /match/${matchId}.`)}
      onNavigate={onNavigate}
      onMake={(roleId) => {
        setBusy({ kind: 'make', roleId });
        setNotice(undefined);
        window.setTimeout(() => {
          setBusy(null);
          setNotice('Gallery: no resume is made here. The page posts {"roleId"} to /api/resumes/path and opens the answer.');
        }, 1800);
      }}
      onOp={onOp}
      onFinalise={() => go('finalise-finding')}
      onMakeDefault={() => {
        setBusy({ kind: 'default' });
        window.setTimeout(() => {
          setBusy(null);
          show({ ...latest.current, resume: { ...latest.current.resume, isDefault: true } });
        }, BEAT_MS);
      }}
      onDownload={(file) => setNotice(`Gallery: no file is made here. The route is /api/resumes/${detail.resume.id}/resume.${file}.`)}
      hrefFor={(file) => `/api/resumes/${detail.resume.id}/resume.${file}`}
      onDownloadOriginal={() => setNotice(`Gallery: no file is made here. The route is /api/resumes/${detail.resume.id}/original.`)}
      originalMissing={screen === 'original-missing'}
      onAsk={(question) => setNotice(`Gallery: this opens the coach with "${question}"`)}
      onRetry={() => undefined}
    />
  );
}

/* ───────────────────────── a second file ───────────────────────── */

const NO_FILES = { status: 'ready' as const, busy: null, noOriginal: new Set<string>(), merged: new Set<string>(), onRetry: () => undefined, onOpenMerge: () => undefined, onDownloadOriginal: () => undefined };

/** The header's button and the line under it, on the blocks: a file being read, or refused. Pick a file to try the page's own checks. */
function UploadStage({ screen, go }: { screen: 'adding' | 'upload-too-many' | 'upload-unreadable'; go: (next: Screen) => void }) {
  const [reading, setReading] = useState<string | undefined>(screen === 'adding' ? 'riya-kapoor-2025.pdf' : undefined);
  const [failure, setFailure] = useState<UploadFailure | undefined>(screen === 'upload-too-many' ? TOO_MANY : screen === 'upload-unreadable' ? UNREADABLE : undefined);
  const [filename, setFilename] = useState<string | undefined>(screen === 'adding' ? undefined : 'riya-kapoor-2025.pdf');

  const pick = (file: File) => {
    setFilename(file.name);
    const refused = checkFile(file);
    if (refused) {
      setFailure(refused);
      return;
    }
    setFailure(undefined);
    setReading(file.name);
    window.setTimeout(() => go('merge'), 3000);
  };

  return (
    <>
      <ResumeHeader route={{ screen: 'blocks' }} blocks={BLOCKS.blocks.length} onNavigate={() => undefined} onUpload={pick} uploading={reading !== undefined} />
      <UploadStrip reading={reading} filename={filename} failure={failure} onRetry={() => setFailure(undefined)} onDismiss={() => setFailure(undefined)} />
      <BlocksView data={BLOCKS} status="ready" onRetry={() => undefined} back={{ screen: 'resume' }} onNavigate={() => undefined} files={{ ...NO_FILES, files: FILES.slice(0, 1) }} />
    </>
  );
}

/** Your blocks after a second file: the merge result on top, other wordings and a flag on two blocks, and Your files. */
function FilesStage({ screen, go }: { screen: 'merge' | 'blocks-files'; go: (next: Screen) => void }) {
  const [open, setOpen] = useState(screen === 'merge');
  const [noOriginal, setNoOriginal] = useState<ReadonlySet<string>>(new Set());
  const [busy, setBusy] = useState<Busy | null>(null);
  const [said, setSaid] = useState('');

  const props: BlocksViewProps = {
    data: BLOCKS_WITH_FILES,
    status: 'ready',
    onRetry: () => undefined,
    back: { screen: 'resume' },
    onNavigate: () => go('draft'),
    merge: open ? MERGE_ENTRY : null,
    onCloseMerge: () => setOpen(false),
    files: {
      files: FILES,
      status: 'ready',
      busy,
      noOriginal,
      merged: new Set(['file_2025']),
      onRetry: () => undefined,
      onOpenMerge: () => setOpen(true),
      onDownloadOriginal: (resumeId) => {
        setBusy({ kind: 'original', resumeId });
        window.setTimeout(() => {
          setBusy(null);
          // the first file was stored before originals were kept: the page hides its button
          if (resumeId === UPLOAD_DETAIL.resume.id) setNoOriginal((n) => new Set([...n, resumeId]));
          else setSaid(`Gallery: no file is made here. The route is /api/resumes/${resumeId}/original.`);
        }, BEAT_MS);
      },
    },
  };
  return (
    <>
      <BlocksView {...props} />
      <p className="rs-fine" role="status">
        {said}
      </p>
    </>
  );
}

/* ───────────────────────── finalise ───────────────────────── */

type FinaliseScreen = 'finalise' | 'finalise-finding' | 'finalise-done' | 'finalise-open' | 'finalise-grammar' | 'finalise-grammar-failed' | 'finalise-grammar-none';

interface FinaliseShown {
  detail: ResumeDetail;
  status: 'idle' | 'running' | 'ready';
  report?: FinaliseReport;
  makeDefault: boolean;
}

const START: Record<FinaliseScreen, FinaliseShown> = {
  finalise: { detail: PATH_DETAIL, status: 'idle', makeDefault: false },
  'finalise-finding': { detail: PATH_DETAIL, status: 'ready', report: REPORT_FINDING, makeDefault: false },
  'finalise-done': { detail: DONE_DETAIL, status: 'ready', report: REPORT_DONE, makeDefault: false },
  'finalise-grammar': { detail: GRAMMAR_DETAIL, status: 'idle', makeDefault: false },
  'finalise-grammar-failed': { detail: PATH_DETAIL, status: 'idle', makeDefault: false },
  'finalise-grammar-none': { detail: PATH_DETAIL, status: 'idle', makeDefault: false },
  'finalise-open': { detail: DONE_DETAIL, status: 'ready', report: { ...REPORT_DONE, lint: LINT_ONE_OPEN }, makeDefault: false },
};

function FinaliseStage({ screen, go }: { screen: FinaliseScreen; go: (next: Screen) => void }) {
  const [shown, setShown] = useState<FinaliseShown>(START[screen]);
  const [busy, setBusy] = useState<Busy | null>(null);
  const [notice, setNotice] = useState<string | undefined>();
  const resumeId = START[screen].detail.resume.id;
  const [grammar, setGrammar] = useState<GrammarState>(() =>
    screen === 'finalise-grammar'
      ? { id: resumeId, status: 'ready', proposals: GRAMMAR_PROPOSALS, ran: true, kept: [] }
      : screen === 'finalise-grammar-failed'
        ? { id: resumeId, status: 'ready', proposals: [], ran: false, kept: [] }
        : screen === 'finalise-grammar-none'
          ? { id: resumeId, status: 'ready', proposals: [], ran: true, kept: [] }
          : { status: 'idle', proposals: [], ran: true, kept: [] },
  );

  const checkGrammar = () => {
    setBusy({ kind: 'grammar' });
    setGrammar({ id: resumeId, status: 'running', proposals: [], ran: true, kept: [] });
    window.setTimeout(() => {
      setBusy(null);
      setGrammar({ id: resumeId, status: 'ready', proposals: shown.detail === GRAMMAR_DETAIL ? GRAMMAR_PROPOSALS : [], ran: screen !== 'finalise-grammar-failed', kept: [] });
    }, 2 * BEAT_MS);
  };
  const acceptProposal = (bulletId: string, text: string) => {
    setBusy({ kind: 'op', op: 'edit', target: bulletId });
    window.setTimeout(() => {
      setBusy(null);
      setShown((s) => {
        const next = applyOp(s.detail, { op: 'edit', bulletId, text });
        return typeof next === 'string' ? s : { ...s, detail: next, status: 'idle', report: undefined };
      });
    }, BEAT_MS);
  };

  const run = ({ makeDefault, answers }: { makeDefault: boolean; answers: FindingAnswer[] }) => {
    setBusy({ kind: 'finalise' });
    setShown((s) => ({ ...s, status: 'running', makeDefault }));
    window.setTimeout(() => {
      setBusy(null);
      setShown((s) => {
        // the first run finds the typed figure; the second, with an answer, finalises
        if (!s.report) return { ...s, status: 'ready', report: REPORT_FINDING };
        const undone = answers.some((a) => a.choice === 'undo');
        const reset = undone ? applyOp(PATH_DETAIL, { op: 'reset', bulletId: answers[0].bulletId }) : DONE_DETAIL;
        const base = typeof reset === 'string' ? DONE_DETAIL : reset;
        const resume = { ...base.resume, state: 'final' as const, finalisedAt: '2026-11-10T09:02:00Z', isDefault: makeDefault };
        return { detail: { ...base, resume, canDownload: true }, status: 'ready', report: REPORT_DONE, makeDefault };
      });
    }, 2 * BEAT_MS);
  };

  return (
    <FinaliseView
      detail={shown.detail}
      status={shown.status}
      report={shown.report}
      wantsDefault={shown.makeDefault}
      busy={busy}
      notice={notice}
      onRun={run}
      onMakeDefault={() => setShown((s) => ({ ...s, detail: { ...s.detail, resume: { ...s.detail.resume, isDefault: true } } }))}
      onDownload={(file) => setNotice(`Gallery: no file is made here. The route is /api/resumes/${shown.detail.resume.id}/resume.${file}.`)}
      hrefFor={(file) => `/api/resumes/${shown.detail.resume.id}/resume.${file}`}
      onNavigate={() => go('draft')}
      grammar={grammar}
      onCheckGrammar={checkGrammar}
      onAcceptProposal={acceptProposal}
      onKeepMine={(bulletId) => setGrammar((g) => ({ ...g, kept: [...g.kept, bulletId] }))}
    />
  );
}

/* ───────────────────────── the coach's second wording ───────────────────────── */

const NOOP = () => undefined;

function RewriteStage() {
  const [phase, setPhase] = useState<RewritePhase>('offer');
  const accept = () => {
    setPhase('saving');
    window.setTimeout(() => setPhase('accepted'), BEAT_MS * 2);
  };
  return (
    <div className="rs-stack">
      <h1 className="serif rs-title" data-rs-h1 tabIndex={-1}>
        The coach proposes a wording
      </h1>
      <p className="rs-lede">Under a coach answer in the chat. Nothing in the draft changes until Accept succeeds.</p>
      <p className="kicker">To accept (this one works)</p>
      <RewriteCard rewrite={REWRITE_FIXTURE} phase={phase} onAccept={accept} onEdit={NOOP} onDismiss={() => setPhase('dismissed')} onOpen={NOOP} />
      {phase !== 'offer' && (
        <button type="button" className="btn btn-sm btn-quiet rs-btn" onClick={() => setPhase('offer')}>
          Start again
        </button>
      )}
      <p className="kicker">Accepted</p>
      <RewriteCard rewrite={REWRITE_FIXTURE} phase="accepted" onAccept={NOOP} onEdit={NOOP} onDismiss={NOOP} onOpen={NOOP} />
      <p className="kicker">The draft has changed (a 4xx)</p>
      <RewriteCard rewrite={REWRITE_FIXTURE} phase="changed" onAccept={NOOP} onEdit={NOOP} onDismiss={NOOP} onOpen={NOOP} />
    </div>
  );
}
