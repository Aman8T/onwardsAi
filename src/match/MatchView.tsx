/**
 * The match: the posting read on its own, a band in words (never a number), the radar of "you" against "role
 * expects", the hard requirements checked first, every requirement with the evidence behind its status, and up to
 * three questions that close the gaps.
 *
 * It renders whatever has arrived. While the match streams, requirements appear with "Checking…" until their rows
 * land and the band waits for the result code computed; nothing is guessed on the page. The view is presentational:
 * the screen in `MatchRoot` feeds it from the store, the dev gallery from fixtures.
 */
import { useRef, useState } from 'react';
import type { Coverage, GateAnswer, MatchResult, Posting, Question, Radar as RadarData } from '../../shared/match';
import { Radar } from '../components/Radar';
import type { RadarAxis } from '../components/Radar';
import { NARROW_QUERY, plural, timeAgo, useMediaQuery } from '../lib/ui';
import { GateList } from './GateList';
import { BAND_LABEL, CONFIDENCE_LABEL, WORK_MODE_LABEL, bandNote, countChips } from './model';
import { StatusChip } from './parts';
import { Questions } from './Questions';
import type { RecheckState } from './Questions';
import { RequirementTable } from './RequirementTable';
import type { AnswerInput, MatchFact } from './types';

export interface MatchViewProps {
  posting?: Posting;
  facts: readonly MatchFact[];
  coverage: readonly Coverage[];
  gates?: readonly GateAnswer[];
  blocked: boolean;
  radar?: RadarData;
  questions: readonly Question[];
  /** The finished result: band, counts and confidence. Absent while streaming. */
  result?: MatchResult;
  reading: boolean;
  /** A calm line about what is being done right now. */
  step?: string;
  createdAt?: string;
  recheck: RecheckState;
  /** Draw the radar from the centre (it arrived live). */
  reveal?: boolean;
  onAnswer: (answers: AnswerInput[]) => void;
  onBuildResume: () => void;
}

/** Rows shown on a phone before "Show the other N". */
const PHONE_ROWS = 5;

export function MatchView(props: MatchViewProps) {
  const { posting, facts, coverage, gates, blocked, radar, questions, result, reading, step, createdAt, recheck, reveal = false, onAnswer, onBuildResume } = props;
  const narrow = useMediaQuery(NARROW_QUERY);
  const firstQuestion = useRef<HTMLTextAreaElement | null>(null);
  const finalCoverage = result?.coverage ?? coverage;

  const goQuestions = () => {
    const box = firstQuestion.current;
    if (!box) return;
    box.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    box.focus({ preventScroll: true });
  };

  const meta = [posting?.location, posting?.workMode ? WORK_MODE_LABEL[posting.workMode] : ''].filter(Boolean).join(', ');
  const asked = createdAt ? Date.parse(createdAt) : NaN;

  return (
    <>
      <section className="mt-intro">
        <p className="kicker">
          Posting match{posting?.company ? ` · ${posting.company}` : ''}
          {reading ? ' · reading now' : Number.isFinite(asked) ? ` · pasted ${timeAgo(asked)}` : ''}
        </p>
        <h1 className="serif mt-title" data-mt-h1 tabIndex={-1}>
          {posting?.title ?? 'Reading the posting…'}
        </h1>
        {posting && (
          <p className="mt-lede">
            {meta ? `${meta} · ` : ''}
            <span className="numeral">{posting.requirements.length}</span> {posting.requirements.length === 1 ? 'requirement' : 'requirements'} read from the posting · the posting was
            treated as data, not instructions
            {posting.anomalies.length > 0 ? ' · it held text aimed at an AI, which was ignored' : ''}
          </p>
        )}
        {posting && <PostingDisclosure posting={posting} />}
      </section>

      <section className="panel mt-panel mt-summary" aria-labelledby="mt-band-h">
        <div className="mt-band">
          <span className="kicker">Match</span>
          {result ? (
            <>
              <h2 id="mt-band-h" className="serif mt-band-word" data-band={result.band}>
                {BAND_LABEL[result.band]}
              </h2>
              {posting && <p className="mt-muted">{bandNote(result, posting)}</p>}
            </>
          ) : (
            <>
              <h2 id="mt-band-h" className="mt-band-wait">
                {reading ? 'Reading the evidence' : 'No result yet'}
              </h2>
              <p className="mt-muted" role="status">
                {step ?? 'Checking each requirement against what you have shown.'}
              </p>
            </>
          )}
        </div>

        {result && (
          <>
            <ul className="mt-counts" aria-label="Requirements by status">
              {countChips(result.counts).map(({ status, n }) => (
                <li key={status}>
                  <StatusChip status={status} count={n} />
                </li>
              ))}
            </ul>
            <div className="mt-conf">
              <span className="kicker">Evidence confidence</span>
              <span className="mt-conf-word" data-confidence={result.confidence}>
                {CONFIDENCE_LABEL[result.confidence]}
              </span>
              {result.confidenceNote && <p className="mt-muted">{result.confidenceNote}</p>}
            </div>
            <div className="mt-actions mt-summary-actions">
              {questions.length > 0 && (
                <button type="button" className="btn btn-quiet mt-btn" onClick={goQuestions}>
                  Close the gaps · <span className="numeral">{questions.length}</span> {questions.length === 1 ? 'question' : 'questions'}
                </button>
              )}
              <button type="button" className="btn btn-primary mt-btn" onClick={onBuildResume}>
                Build my resume for this role
              </button>
            </div>
          </>
        )}
      </section>

      <section className="mt-pair" aria-label="The shape of it and the hard requirements">
        <div className="panel mt-panel">
          <div className="mt-panel-head">
            <h2 className="kicker mt-h2">You against what the role expects</h2>
            <p className="mt-legend" aria-hidden="true">
              <span>
                <svg width="24" height="8" viewBox="0 0 24 8" focusable="false">
                  <line className="mt-lg-you" x1="0" y1="4" x2="24" y2="4" />
                </svg>
                You
              </span>
              <span>
                <svg width="24" height="8" viewBox="0 0 24 8" focusable="false">
                  <line className="mt-lg-role" x1="0" y1="4" x2="24" y2="4" />
                </svg>
                Role expects
              </span>
            </p>
          </div>
          {radar ? <RadarPanel radar={radar} reveal={reveal} /> : <p className="mt-muted mt-wait">Drawn once the evidence is checked.</p>}
        </div>

        <div className="panel mt-panel">
          <h2 className="kicker mt-h2">Hard requirements, checked first</h2>
          {posting && gates ? <GateList posting={posting} gates={gates} facts={facts} blocked={blocked} /> : <p className="mt-muted mt-wait">Checked as soon as the posting is read.</p>}
        </div>
      </section>

      {posting && (
        <section className="panel mt-panel" aria-labelledby="mt-all-h">
          <div className="mt-panel-head">
            <h2 id="mt-all-h" className="kicker mt-h2">
              Every requirement, with your evidence
            </h2>
            <span className="mt-muted">Sorted by how strongly the posting asks, then unmet first</span>
          </div>
          <RequirementTable posting={posting} coverage={finalCoverage} facts={facts} limit={narrow ? PHONE_ROWS : undefined} settled={!!result} />
          <p className="mt-fine">Every status cites a line, a link or something you told us. Nothing here was written from the posting itself.</p>
        </section>
      )}

      {result && (questions.length > 0 || recheck.status === 'done') && (
        <section id="close" className="panel mt-panel" aria-labelledby="mt-close-h">
          <div className="mt-panel-head mt-panel-head-col">
            <h2 id="mt-close-h" className="kicker mt-h2">
              {questions.length > 0 ? (
                <>
                  Close the gaps · <span className="numeral">{questions.length}</span> {questions.length === 1 ? 'question' : 'questions'}
                </>
              ) : (
                'Close the gaps'
              )}
            </h2>
            <p className="mt-lede">Your answers become facts on your profile, marked "from you". They are never rewritten into claims you didn't make.</p>
          </div>
          {questions.length > 0 ? (
            <Questions
              questions={questions}
              recheck={recheck}
              onSubmit={onAnswer}
              firstRef={(el) => {
                firstQuestion.current = el;
              }}
            />
          ) : (
            <p className="mt-status" role="status">
              {recheck.message ?? 'Every question is answered.'}
            </p>
          )}
        </section>
      )}

      <p className="mt-fine mt-footer">
        Role expectations are built from the posting's own wording, not by a model. This service uses the ESCO classification of the European Commission.
      </p>
    </>
  );
}

/* ───────────────────────── the radar ───────────────────────── */

export function RadarPanel({ radar, reveal = false }: { radar: RadarData; reveal?: boolean }) {
  const [picked, setPicked] = useState<string | null>(null);
  const axes: RadarAxis[] = radar.axes.map((a) => ({ id: a.domain, label: a.domain, value: a.you, secondary: a.role }));
  const sel = radar.axes.find((a) => a.domain === picked);

  if (axes.length < 3) {
    // not enough spokes for a polygon: the same two numbers as a quiet list
    return (
      <ul className="mt-radar-list" aria-label="You against what the role expects">
        {radar.axes.map((a) => (
          <li key={a.domain}>
            <span>{a.domain}</span>
            <span className="numeral">
              {a.you} / {a.role}
            </span>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className="mt-radar">
      <Radar
        axes={axes}
        axisSetKey={`match:${axes.map((a) => a.id).join('|')}`}
        series={{ primary: 'You', secondary: 'Role expects' }}
        selectedId={picked}
        axisState="pressed"
        reveal={reveal}
        ariaLabel={`Your ${plural(axes.length, 'domain')} against what the role expects`}
        tableCaption="Values along each spoke, 0 to 100. Role weights come from the posting's wording."
        onSelect={(id) => setPicked((p) => (p === id ? null : id))}
        onBackgroundClick={() => setPicked(null)}
      />
      <p className="mt-readout" aria-live="polite">
        {sel ? (
          <>
            {sel.domain}: you <span className="numeral">{sel.you}</span>, role expects <span className="numeral">{sel.role}</span>.
          </>
        ) : (
          'Select a domain to read its two values.'
        )}
      </p>
    </div>
  );
}

/* ───────────────────────── the posting we saw ───────────────────────── */

/** The posting's own text, its anomalies and its links, for anyone who wants to check what was read. All plain text. */
function PostingDisclosure({ posting }: { posting: Posting }) {
  return (
    <details className="mt-details">
      <summary>Read the posting we saw</summary>
      <div className="mt-details-body">
        <pre className="mt-posting-raw">{posting.rawText}</pre>
        {posting.anomalies.length > 0 && (
          <div>
            <p className="kicker">Text aimed at an AI, reported and ignored</p>
            <ul className="mt-plainlist">
              {posting.anomalies.map((a, i) => (
                <li key={i}>
                  <q>{a}</q>
                </li>
              ))}
            </ul>
          </div>
        )}
        {posting.links.length > 0 && (
          <div>
            <p className="kicker">Addresses written in the posting, listed and never opened</p>
            <ul className="mt-plainlist">
              {posting.links.map((l) => (
                <li key={l} className="numeral">
                  {l}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}
