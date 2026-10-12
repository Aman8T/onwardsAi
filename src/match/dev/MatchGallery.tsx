/**
 * Dev gallery (`/dev/match`, development builds only): the five match screens fed by the two persona fixtures, with
 * no server and no account. Pick a persona and a screen in the bar, or link straight to one:
 *
 *   /dev/match?persona=meera&screen=match
 *   screens: paste · paste-402 · paste-422 · stream · match · resume · resume-decisions · cover · cover-answer
 *
 * `resume-decisions` and `cover-answer` are synthetic variations of the fixture (decisions waiting on the person,
 * an answer "from you"); everything else is the fixture as the M1 service produced it. The decisions really work
 * (settling one updates the preview), downloads and the re-check are stubbed.
 */
import { useEffect, useMemo, useState } from 'react';
import type { DecisionChoice, ResumeVariant } from '../../../shared/match';
import { Brand } from '../../components/Brand';
import { CoverView } from '../CoverView';
import { FactSheet } from '../FactSheet';
import { MatchView } from '../MatchView';
import { factIndex } from '../model';
import { PasteView } from '../PasteView';
import type { RecheckState } from '../Questions';
import { ResumeView } from '../ResumeView';
import { matchPath } from '../route';
import type { MatchRoute } from '../route';
import { FIXTURES, PERSONA_NAME, applyChoice, streamedRows, withAnswer, withDecisions } from './fixtures';
import type { Fixture, PersonaId } from './fixtures';
import '../../styles/match.css';

const SCREENS = [
  ['paste', 'Paste'],
  ['paste-402', 'Paste · used the free posting'],
  ['paste-422', 'Paste · no requirements'],
  ['stream', 'Match · streaming'],
  ['match', 'Match'],
  ['resume', 'Resume'],
  ['resume-decisions', 'Resume · decisions'],
  ['cover', 'Cover note'],
  ['cover-answer', 'Cover note · with an answer'],
] as const;
type Screen = (typeof SCREENS)[number][0];

const isScreen = (s: string | null): s is Screen => SCREENS.some(([id]) => id === s);

function readParams(): { persona: PersonaId; screen: Screen } {
  const p = new URLSearchParams(window.location.search);
  const persona = p.get('persona');
  const screen = p.get('screen');
  return { persona: persona === 'riya' ? 'riya' : 'meera', screen: isScreen(screen) ? screen : 'paste' };
}

const noop = () => {};

export function MatchGallery() {
  const [{ persona, screen }, setPick] = useState(readParams);

  const go = (next: Partial<{ persona: PersonaId; screen: Screen }>) => {
    const to = { persona, screen, ...next };
    setPick(to);
    window.history.replaceState(null, '', `/dev/match?persona=${to.persona}&screen=${to.screen}`);
  };

  // the in-app routes the views link to: the gallery maps them onto its own screens
  const onNavigate = (route: MatchRoute) => {
    const screenFor: Record<MatchRoute['screen'], Screen> = { paste: 'paste', match: 'match', resume: 'resume', cover: 'cover' };
    go({ screen: screenFor[route.screen] });
  };

  return (
    <div className="mt-view">
      <header className="mt-header">
        <Brand />
        <span className="kicker">Dev only · the two stored matches from backend/tests/data/match</span>
        <div className="mt-header-end">
          <div role="group" aria-label="Persona" className="mt-actions">
            {(Object.keys(FIXTURES) as PersonaId[]).map((id) => (
              <button key={id} type="button" className="btn btn-sm mt-btn-sm" aria-pressed={persona === id} onClick={() => go({ persona: id })}>
                {PERSONA_NAME[id]}
              </button>
            ))}
          </div>
        </div>
        <nav className="mt-nav" aria-label="Screens" style={{ flex: '1 1 100%', marginLeft: 0 }}>
          {SCREENS.map(([id, label]) => (
            <button key={id} type="button" className={`mt-navbtn${screen === id ? ' is-current' : ''}`} aria-pressed={screen === id} onClick={() => go({ screen: id })}>
              {label}
            </button>
          ))}
        </nav>
      </header>
      <main className="mt-main" key={`${persona}:${screen}`}>
        <Stage persona={persona} screen={screen} onNavigate={onNavigate} />
      </main>
    </div>
  );
}

function Stage({ persona, screen, onNavigate }: { persona: PersonaId; screen: Screen; onNavigate: (route: MatchRoute) => void }) {
  const base = FIXTURES[persona];
  const fx = useMemo<Fixture>(() => (screen === 'cover-answer' ? withAnswer(base) : base), [base, screen]);
  const id = `${persona}-demo`;

  switch (screen) {
    case 'paste':
    case 'paste-402':
    case 'paste-422':
      return <PasteStage screen={screen} persona={persona} onNavigate={onNavigate} />;
    case 'stream':
      return <StreamStage fx={fx} />;
    case 'match':
      return <MatchStage fx={fx} onNavigate={onNavigate} id={id} />;
    case 'resume':
    case 'resume-decisions':
      return <ResumeStage fx={fx} id={id} withDecisionsOn={screen === 'resume-decisions'} onNavigate={onNavigate} />;
    default:
      return (
        <CoverView
          posting={fx.posting}
          facts={fx.facts}
          coverage={fx.result.coverage}
          note={fx.cover}
          matchId={id}
          onNavigate={onNavigate}
          hrefPdf={`/api/match/${id}/cover.pdf`}
          onDownload={noop}
        />
      );
  }
}

/* ───────────────────────── paste ───────────────────────── */

function PasteStage({ screen, persona, onNavigate }: { screen: Screen; persona: PersonaId; onNavigate: (route: MatchRoute) => void }) {
  const [draft, setDraft] = useState({ text: screen === 'paste' ? '' : FIXTURES[persona].posting.rawText, url: '' });
  const other = persona === 'meera' ? 'riya' : 'meera';
  return (
    <PasteView
      draft={draft}
      onDraft={(patch) => setDraft((d) => ({ ...d, ...patch }))}
      onSubmit={() => onNavigate({ screen: 'match', id: 'demo' })}
      busy={false}
      notice={
        screen === 'paste-422'
          ? "We couldn't find any requirements in that text. Paste the full posting, including the requirements or qualifications section."
          : undefined
      }
      paywall={screen === 'paste-402'}
      onDismissPaywall={noop}
      showFree={screen !== 'paste-402'}
      recent={[FIXTURES[other].posting, FIXTURES[persona].posting].map((p, i) => ({
        id: `r${i}`,
        title: p.title,
        company: p.company,
        band: FIXTURES[i === 0 ? other : persona].result.band,
        createdAt: '2026-10-10T09:00:00Z',
      }))}
      roles={[
        { id: 'a', title: 'Growth marketing manager' },
        { id: 'b', title: 'Product marketing manager' },
        { id: 'c', title: 'Head of digital' },
      ]}
      onPickRole={noop}
      onNavigate={onNavigate}
    />
  );
}

/* ───────────────────────── match ───────────────────────── */

const FAKE_RECHECK_MS = 900;

function MatchStage({ fx, onNavigate, id }: { fx: Fixture; id: string; onNavigate: (route: MatchRoute) => void }) {
  const [recheck, setRecheck] = useState<RecheckState>({ status: 'idle' });
  useEffect(() => {
    if (recheck.status !== 'saving') return;
    const t = window.setTimeout(() => setRecheck({ status: 'done', message: 'Saved and re-checked (gallery: nothing was sent).' }), FAKE_RECHECK_MS);
    return () => window.clearTimeout(t);
  }, [recheck.status]);
  return (
    <MatchView
      posting={fx.posting}
      facts={fx.facts}
      coverage={fx.result.coverage}
      gates={fx.result.gates}
      blocked={fx.result.blocked}
      radar={fx.result.radar}
      questions={fx.result.questions}
      result={fx.result}
      reading={false}
      createdAt={new Date(Date.now() - 2 * 60_000).toISOString()}
      recheck={recheck}
      reveal
      onAnswer={() => setRecheck({ status: 'saving' })}
      onBuildResume={() => onNavigate({ screen: 'resume', id })}
    />
  );
}

function StreamStage({ fx }: { fx: Fixture }) {
  const n = Math.ceil(fx.posting.requirements.length / 2);
  return (
    <MatchView
      posting={fx.posting}
      facts={fx.facts}
      coverage={streamedRows(fx, n)}
      gates={fx.result.gates}
      blocked={fx.result.blocked}
      questions={[]}
      reading
      step={`Checked ${n} of ${fx.posting.requirements.length} requirements…`}
      recheck={{ status: 'idle' }}
      onAnswer={noop}
      onBuildResume={noop}
    />
  );
}

/* ───────────────────────── resume ───────────────────────── */

function ResumeStage({ fx, id, withDecisionsOn, onNavigate }: { fx: Fixture; id: string; withDecisionsOn: boolean; onNavigate: (route: MatchRoute) => void }) {
  const [variant, setVariant] = useState<ResumeVariant>(() => (withDecisionsOn ? withDecisions(fx.variant) : fx.variant));
  const [saving, setSaving] = useState<ReadonlySet<string>>(new Set());
  const [factId, setFactId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | undefined>();
  const byId = useMemo(() => factIndex(fx.facts), [fx.facts]);

  const decide = (choice: DecisionChoice) => {
    setSaving((s) => new Set(s).add(choice.bulletId));
    window.setTimeout(() => {
      setVariant((v) => applyChoice(v, choice.bulletId, choice.choice, choice.value));
      setSaving((s) => {
        const next = new Set(s);
        next.delete(choice.bulletId);
        return next;
      });
    }, 350);
  };

  return (
    <>
      <ResumeView
        posting={fx.posting}
        facts={fx.facts}
        variant={variant}
        matchId={id}
        saving={saving}
        notice={notice}
        onDecide={decide}
        onNavigate={onNavigate}
        onDownload={(kind) => setNotice(`Gallery: no ${kind.toUpperCase()} is made here. The route is ${matchPath({ screen: 'resume', id })}.${kind}.`)}
        onFact={(fid) => setFactId(fid)}
        hrefFor={(kind) => `/api/match/${id}/resume.${kind}`}
      />
      <FactSheet id={factId} fact={factId ? byId.get(factId) : undefined} onClose={() => setFactId(null)} />
    </>
  );
}
