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
 *   blocks            your blocks, grouped by job
 *   loading · error   the two waiting states
 *
 * The buttons work against a stand-in for the server (`applyOp` in fixtures.ts) that answers after a beat; whatever
 * would leave the page or fetch a file says where it would go instead.
 */
import { useRef, useState } from 'react';
import { Brand } from '../../components/Brand';
import { BlocksView } from '../BlocksView';
import { FinaliseView } from '../FinaliseView';
import { StatePanel } from '../parts';
import { ResumePage } from '../ResumePage';
import type { ResumeQuery } from '../route';
import type { Busy, FinaliseReport, FindingAnswer, ResumeDetail, ResumeList, ResumeOp } from '../types';
import { BLOCKS, DONE_DETAIL, LINT_ONE_OPEN, LIST, PATH_DETAIL, PRESSED_BULLET, REPORT_DONE, REPORT_FINDING, UPLOAD_DETAIL, applyOp } from './fixtures';
import '../../styles/resume.css';

const SCREENS = [
  ['upload', 'Uploaded · final, default'],
  ['draft', 'Built for a role · draft'],
  ['making', 'Making a resume'],
  ['finalise', 'Finalise · before'],
  ['finalise-finding', 'Finalise · one finding'],
  ['finalise-done', 'Finalise · done'],
  ['finalise-open', 'Finalise · a check open'],
  ['blocks', 'Your blocks'],
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
      return <PageStage screen={screen} go={go} />;
    case 'finalise':
    case 'finalise-finding':
    case 'finalise-done':
    case 'finalise-open':
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

function PageStage({ screen, go }: { screen: 'upload' | 'draft' | 'making'; go: (next: Screen) => void }) {
  const [detail, setDetail] = useState<ResumeDetail>(screen === 'upload' ? UPLOAD_DETAIL : PATH_DETAIL);
  const [list, setList] = useState<ResumeList>(LIST);
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
      initialSelected={screen === 'draft' && detail.resume.id === PATH_DETAIL.resume.id ? PRESSED_BULLET : undefined}
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
      onAsk={(question) => setNotice(`Gallery: this opens the coach with "${question}"`)}
      onRetry={() => undefined}
    />
  );
}

/* ───────────────────────── finalise ───────────────────────── */

type FinaliseScreen = 'finalise' | 'finalise-finding' | 'finalise-done' | 'finalise-open';

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
  'finalise-open': { detail: DONE_DETAIL, status: 'ready', report: { ...REPORT_DONE, lint: LINT_ONE_OPEN }, makeDefault: false },
};

function FinaliseStage({ screen, go }: { screen: FinaliseScreen; go: (next: Screen) => void }) {
  const [shown, setShown] = useState<FinaliseShown>(START[screen]);
  const [busy, setBusy] = useState<Busy | null>(null);
  const [notice, setNotice] = useState<string | undefined>();

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
    />
  );
}
