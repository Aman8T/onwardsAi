/**
 * The debrief (board 5 of the "Practice the interview" canvas, board 10 on a phone), presentational: the debrief arrives
 * as a prop, everything the person can do goes out as a callback. `DebriefScreen` feeds it from the server; the dev page
 * feeds it from fixtures.
 *
 * Top to bottom: how it went (one square per question, the counts in words, a line about the role's asks, the one thing to
 * work on first), the work sample's checks when it was one, then one card per question with the words its verdict rests
 * on marked inside the person's own answer, and beside them the transcript. On a phone the first thing to work on opens
 * and the other questions fold into rows that open. There is no score: counts, words, and quotes the person can check.
 */
import { useReducedMotion } from 'motion/react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { Debrief, InterviewKind } from '../../../shared/interview';
import { Pip, countsLine, totalOf } from '../Pips';
import { KIND_LABEL, KEPT_LINE, VERDICT_LABEL } from '../words';
import { NOTHING_SHARED, dateOf, sentenceOf } from '../hub/words';
import type { PracticeNav } from '../hub/nav';
import { FeaturedCard, ResultCard, ResultRow } from './ResultCard';
import type { CardContext, FixPlacing } from './ResultCard';
import { TranscriptPanel } from './Transcript';
import { ChecksPanel, WorkPanel } from './WorkParts';
import { turnIds } from './quote';
import '../../styles/practice.css';

export interface DebriefViewProps {
  debrief: Debrief;
  /** The phone layout: the first thing to work on opens, the other questions fold into rows. */
  narrow: boolean;
  nav: PracticeNav;
  /** Start one question again; rejects with the reason when it cannot (the screen shows the server's sentence). */
  onAgain: (slotId: string) => Promise<void>;
  /** Keep (or take back) what the person said as a fact marked "From you". */
  onFact: (slotId: string, add: boolean) => Promise<void>;
  /** Delete the interview and its transcript now. */
  onDelete: () => Promise<void>;
  /** Where its Fix steps can go (I2); absent: they are steps to read. */
  fix?: FixPlacing;
}

const NUMBER_WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'];
const countWord = (n: number) => NUMBER_WORDS[n] ?? String(n);

/** Another kind to suggest at the end: the one that asks about the other half of a person's evidence. */
const OTHER_KIND: Record<InterviewKind, InterviewKind> = {
  role: 'resume',
  resume: 'stories',
  stories: 'resume',
  work: 'resume',
  redrill: 'resume',
};

/** The summary line, with "add what you said to your profile" linked to the new fact it points at. */
function SummaryLine({ text, factSlot, jump }: { text: string; factSlot: string | undefined; jump: (id: string) => void }) {
  const phrase = 'add what you said to your profile';
  const at = text.indexOf(phrase);
  if (at < 0 || !factSlot) return <>{text}</>;
  const id = `fact-${factSlot}`;
  return (
    <>
      {text.slice(0, at)}
      <a
        href={`#${id}`}
        onClick={(e) => {
          e.preventDefault();
          jump(id);
        }}
      >
        {phrase}
      </a>
      {text.slice(at + phrase.length)}
    </>
  );
}

export function DebriefView({ debrief, narrow, nav, onAgain, onFact, onDelete, fix }: DebriefViewProps) {
  const base = useId();
  const reduced = useReducedMotion() ?? false;
  const titleRef = useRef<HTMLHeadingElement>(null);
  const { results, counts } = debrief;
  const total = totalOf(counts);
  const ids = useMemo(() => turnIds(debrief.transcript), [debrief.transcript]);
  const featured = narrow ? results.findIndex((r) => r.slotId === debrief.firstSlotId) : -1;

  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [active, setActive] = useState<string | null>(null);
  const [want, setWant] = useState<string | null>(null);
  const [againBusy, setAgainBusy] = useState<string | null>(null);
  const [againError, setAgainError] = useState<{ where: string; message: string } | null>(null);
  const [factBusy, setFactBusy] = useState<string | null>(null);
  const [factError, setFactError] = useState<{ slotId: string; message: string } | null>(null);

  // the page opens at its title, for a reader who arrived by keyboard or with a screen reader
  useEffect(() => {
    titleRef.current?.focus();
  }, []);

  // Where a link wanted to go, once what it points at is on the page (a folded row opened, the transcript unfolded).
  useEffect(() => {
    if (!want) return;
    const el = document.getElementById(want);
    if (!el) {
      setWant(null);
      return;
    }
    el.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: want.startsWith('t-') ? 'center' : 'start' });
    el.focus({ preventScroll: true });
    if (want.startsWith('t-')) setActive(want);
    setWant(null);
  }, [want, reduced]);

  const jump = (id: string) => {
    if (narrow) {
      const slot = id.startsWith('fact-') ? id.slice(5) : /^q\d+$/.test(id) ? results[Number(id.slice(1)) - 1]?.slotId : undefined;
      if (slot) setOpen((prev) => new Set(prev).add(slot));
      if (id.startsWith('t-')) setTranscriptOpen(true);
    }
    setWant(id);
  };

  const again = async (slotId: string, where: string) => {
    setAgainBusy(slotId);
    setAgainError(null);
    try {
      await onAgain(slotId);
    } catch (err) {
      setAgainError({ where, message: sentenceOf(err) });
    } finally {
      setAgainBusy(null);
    }
  };

  const fact = async (slotId: string, add: boolean) => {
    setFactBusy(slotId);
    setFactError(null);
    try {
      await onFact(slotId, add);
    } catch (err) {
      setFactError({ slotId, message: sentenceOf(err) });
    } finally {
      setFactBusy(null);
    }
  };

  const role = debrief.role;
  const ctx: CardContext = {
    debrief,
    ids,
    ...(role ? { onPrepare: () => nav.prepare(role) } : {}),
    jump,
    onAgain: (slotId) => void again(slotId, slotId),
    againBusy,
    againError,
    onFact: (slotId, add) => void fact(slotId, add),
    factBusy,
    factError,
    ...(fix ? { fix } : {}),
  };
  const redrill = debrief.kind === 'redrill';

  const factSlot = results.find((r) => r.newFact && !r.factAdded)?.slotId;
  const when = dateOf(debrief.createdAt);
  const minutes = debrief.minutes > 0 ? `${debrief.minutes} ${debrief.minutes === 1 ? 'minute' : 'minutes'}` : '';
  const allDefended = total > 0 && counts.defended === total;
  const firstError = againError?.where === 'first' ? againError.message : undefined;
  const other = OTHER_KIND[debrief.kind];

  const toggle = (slotId: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(slotId)) next.delete(slotId);
      else next.add(slotId);
      return next;
    });

  const transcript = (
    <TranscriptPanel debrief={debrief} ids={ids} active={active} narrow={narrow} open={transcriptOpen} onOpen={setTranscriptOpen} onDelete={onDelete} />
  );

  const actions = (
    <div className="pc-actions pc-end">
      {redrill ? (
        <button type="button" className="btn btn-primary pc-btn" onClick={nav.redrill}>
          Back to Re-drill
        </button>
      ) : (
        <button type="button" className="btn btn-primary pc-btn" onClick={() => nav.setup(debrief.kind, debrief.role, debrief.graphRole)}>
          Practise again
        </button>
      )}
      <button type="button" className="btn pc-btn" onClick={() => nav.setup(other)}>
        Try {KIND_LABEL[other]}
      </button>
      {debrief.role ? (
        <button type="button" className="btn btn-quiet pc-btn" onClick={nav.applications}>
          Back to Your applications
        </button>
      ) : (
        <button type="button" className="btn btn-quiet pc-btn" onClick={nav.hub}>
          Back to Practice
        </button>
      )}
    </div>
  );

  return (
    <div className={`pc-page pc-debrief${narrow ? ' pc-narrow' : ''}`}>
      <header className="pc-head">
        <button type="button" className="pc-back" onClick={nav.hub}>
          <span aria-hidden="true">←</span> Practice
        </button>
        <p className="kicker">{['Debrief', KIND_LABEL[debrief.kind], when, minutes].filter(Boolean).join(' · ')}</p>
        <h1 className="serif pc-title" ref={titleRef} tabIndex={-1}>
          {debrief.title}
        </h1>
      </header>

      <section className="panel pc-summary" aria-labelledby={`${base}-sum`}>
        <div className="pc-summary-main">
          <div className="kicker" id={`${base}-sum`}>
            How it went
          </div>
          <div className="pc-pipnav" role="group" aria-label="One square per question, in order">
            {results.map((r, i) => (
              <a
                key={r.slotId}
                href={`#q${i + 1}`}
                className="pc-pipnav-item"
                aria-label={`Question ${i + 1}, ${VERDICT_LABEL[r.verdict]}`}
                onClick={(e) => {
                  e.preventDefault();
                  jump(`q${i + 1}`);
                }}
              >
                <Pip verdict={r.verdict} size="lg" />
                <span className="numeral">{i + 1}</span>
              </a>
            ))}
          </div>
          {debrief.redrill && (
            <p className="serif pc-moved">
              {debrief.redrill.moved} of {debrief.redrill.of} moved up
            </p>
          )}
          <p className="pc-counts">{countsLine(counts)}</p>
          {debrief.redrill && <p className="pc-muted pc-small">Your best verdicts in Progress are updated. A worse answer today never lowers one.</p>}
          {debrief.summary && (
            <p className="pc-summary-line">
              <SummaryLine text={debrief.summary} factSlot={factSlot} jump={jump} />
            </p>
          )}
          {counts.notAssessed > 0 && <p className="pc-muted">Not assessed means not asked, skipped or ended early. It never counts against you.</p>}
          <p className="pc-muted pc-small">No score. Each verdict quotes what you said, so you can check it.</p>
        </div>

        {debrief.firstThing ? (
          <div className="pc-first">
            <div className="kicker">Work on this first</div>
            <p className="serif pc-first-text">{debrief.firstThing}</p>
            <div className="pc-actions">
              {debrief.firstSlotId && (
                <button type="button" className="btn btn-primary pc-btn" disabled={againBusy === debrief.firstSlotId} onClick={() => void again(debrief.firstSlotId as string, 'first')}>
                  {againBusy === debrief.firstSlotId ? 'Starting…' : 'Ask this one again'}
                </button>
              )}
              {role && (
                <button type="button" className="btn pc-btn" onClick={() => nav.prepare(role)}>
                  Open Prepare
                </button>
              )}
            </div>
            {firstError && (
              <p className="pc-danger" role="alert">
                {firstError}
              </p>
            )}
            <p className="pc-muted pc-small">Asking again is free.</p>
          </div>
        ) : allDefended ? (
          <div className="pc-first">
            <div className="kicker">Work on this first</div>
            <p className="serif pc-first-text">You defended everything you were asked.</p>
            <p className="pc-muted pc-small">Ask any question again whenever you like. It is free.</p>
          </div>
        ) : null}
      </section>

      {debrief.kind === 'work' && <WorkPanel debrief={debrief} />}
      {debrief.kind === 'work' && <ChecksPanel debrief={debrief} />}

      <div className="pc-cols">
        <div className="pc-main-col">
          {narrow ? (
            <>
              {featured >= 0 && <FeaturedCard result={results[featured]} index={featured} ctx={ctx} />}
              <section className="pc-others" aria-label={featured >= 0 ? 'The other questions' : 'The questions'}>
                <div className="kicker">{featured >= 0 ? `The other ${results.length - 1}` : `Your ${results.length} questions`}</div>
                <div className="panel pc-acc-list">
                  {results.map((r, i) =>
                    i === featured ? null : <ResultRow key={r.slotId} result={r} index={i} open={open.has(r.slotId)} onToggle={() => toggle(r.slotId)} ctx={ctx} />,
                  )}
                </div>
              </section>
            </>
          ) : (
            <>
              <div className="pc-sec-head">
                <p className="kicker">Your {countWord(results.length)} {results.length === 1 ? 'question' : 'questions'}</p>
                <h2 className="serif pc-h2">Question by question</h2>
                <p className="pc-slotkey">
                  <span className="pc-slotkey-box" aria-hidden="true" />
                  <span className="pc-muted pc-small">A dashed box is for what only you know. We do not guess it.</span>
                </p>
              </div>
              {results.map((r, i) => (
                <ResultCard key={r.slotId} result={r} index={i} ctx={ctx} />
              ))}
            </>
          )}
          {!narrow && actions}
        </div>

        <aside className="pc-side" aria-label="Transcript">
          {transcript}
        </aside>
      </div>

      {narrow && actions}

      <footer className="pc-footer">
        <p>
          {KEPT_LINE} {NOTHING_SHARED}
        </p>
      </footer>
    </div>
  );
}
