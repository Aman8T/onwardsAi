/**
 * One question of the debrief (board 5 of the "Practice the interview" canvas, and board 10 on a phone): the question,
 * what it was about, what the person said with the words the verdict rests on marked, why, and three short columns: Say
 * (a better way to put it, built only from their words and evidence, with a dashed box for what only they know), Avoid
 * (their own words and what to do instead) and Fix (a step, with its time). A new fact from a Defended answer is offered
 * to be kept as "From you". Every verdict links to the moment it was said.
 *
 * Re-drill (I2): a claim that came back shows its previous verdict next to today's; a Fix step can go into a Prepare plan
 * or stay in Practice (the plan of the session's role is chosen first); a claim that could not be defended twice may show
 * "Or say less", a line built from the answer's own words.
 *
 * Nothing here adds a claim: every line is the server's, and a line that was not sent is not drawn. A question that was
 * not assessed has no verdict to explain and says that it never counts against the person.
 */
import { useId, useState } from 'react';
import type { Debrief, FixTask, SlotKind, SlotResult, SlotSource } from '../../../shared/interview';
import type { PreparePlanSummary } from '../../../shared/prepare';
import { VerdictChip } from '../Pips';
import { SLOT_KIND_LABEL, VERDICT_LABEL as VERDICT_WORD } from '../words';
import { clock, dateOf } from '../hub/words';
import { RUNG_LABEL, builtFrom, exchangeFor, markPieces, sayPieces, turnOfQuote } from './quote';
import type { Exchange } from './quote';

/** What a card needs from the screen around it. */
export interface CardContext {
  debrief: Debrief;
  /** An id for each turn of the transcript, parallel to `debrief.transcript`. */
  ids: readonly string[];
  /** A role's prep page, for a role session; absent otherwise (the Fix step then shows no link). */
  onPrepare?: () => void;
  /** Scroll to an address on the page (a turn of the transcript, a card). */
  jump: (id: string) => void;
  onAgain: (slotId: string) => void;
  /** The slot an "ask again" is starting for, if one is. */
  againBusy: string | null;
  /** Where the last "ask again" failed (`first` is the box at the top) and why. */
  againError: { where: string; message: string } | null;
  onFact: (slotId: string, add: boolean) => void;
  factBusy: string | null;
  factError: { slotId: string; message: string } | null;
  /** Where a Fix step can go (I2); absent: the Fix step is a step to read, as before. */
  fix?: FixPlacing;
}

/** Putting a debrief's Fix step into a Prepare plan, or keeping it in Practice. */
export interface FixPlacing {
  /** The person's plans, newest first. */
  plans: readonly PreparePlanSummary[];
  /** The plan offered first: the session's role's, else the newest. */
  defaultPlanId?: string;
  /** The steps already placed, by slot. */
  placed: Readonly<Record<string, FixTask>>;
  busy: string | null;
  error: { slotId: string; message: string } | null;
  /** Place the step of a slot: in a plan, or kept in Practice (no plan). */
  onPlace: (slotId: string, planId?: string) => void;
  /** Take it out again. */
  onUndo: (slotId: string) => void;
  onOpenPlan: (plan: PreparePlanSummary) => void;
  onRedrill: () => void;
}

/** The kind of question, as a chip: told apart by fill and outline, and always in words. */
export function KindChip({ kind }: { kind: SlotKind }) {
  return <span className={`pc-kind pc-kind-${kind}`}>{SLOT_KIND_LABEL[kind]}</span>;
}

/* ───────────────────────── the top row ───────────────────────── */

/** "Question 2 · An ask you show · Shaky", and the moments the person answered, each a link to the transcript. */
export function CardTop({ result, index, ctx, exchange }: { result: SlotResult; index: number; ctx: CardContext; exchange: readonly Exchange[] }) {
  const yours = exchange.filter((e) => e.turn.speaker === 'you');
  return (
    <div className="pc-card-top">
      <span className="kicker">Question {index + 1}</span>
      <KindChip kind={result.kind} />
      <VerdictChip verdict={result.verdict} stamped />
      <span className="pc-grow" />
      {yours.length > 0 && (
        <span className="pc-times">
          {yours.map((e, i) => {
            const id = ctx.ids[e.index];
            return (
              <span key={e.index} className="pc-times-item">
                {i > 0 && (
                  <span className="pc-muted" aria-hidden="true">
                    ·
                  </span>
                )}
                <a href={`#${id}`} onClick={(ev) => jumpTo(ev, id, ctx)}>
                  {i === 0 ? 'At ' : ''}
                  {clock(e.turn.at)}
                </a>
              </span>
            );
          })}
        </span>
      )}
    </div>
  );
}

function jumpTo(ev: { preventDefault: () => void }, id: string, ctx: CardContext) {
  ev.preventDefault();
  ctx.jump(id);
}

/* ───────────────────────── what it was about ───────────────────────── */

/** `role` is a requirement of a role on the person's graph: what the role needs, as the graph reads it, never a posting. */
const ABOUT_LABEL: Record<SlotKind, Record<SlotSource['origin'], string>> = {
  ask: { posting: 'They ask', evidence: 'You show', role: 'The role needs' },
  gap: { posting: 'They ask', evidence: 'Nearest evidence', role: 'The role needs' },
  claim: { posting: 'They ask', evidence: 'You show', role: 'The role needs' },
  story: { posting: 'They ask', evidence: 'The story came from', role: 'The role needs' },
  task: { posting: 'From the task', evidence: 'From', role: 'The role needs' },
};

const citeOf = (s: SlotSource) => (s.origin === 'posting' ? 'Posting' : s.origin === 'role' ? 'Your skill graph' : (s.source ?? 'Resume'));

function About({ kind, sources }: { kind: SlotKind; sources: readonly SlotSource[] }) {
  if (sources.length === 0) return null;
  return (
    <div className="pc-block">
      <div className="kicker">What it was about</div>
      {sources.map((s, i) => (
        <p key={i} className="pc-fact">
          <span className="pc-lbl">{ABOUT_LABEL[kind][s.origin]} </span>“{s.text}” <span className="pc-cite">{citeOf(s)}</span>
          {s.origin === 'posting' && kind === 'gap' && <span className="pc-muted"> · not on your resume</span>}
        </p>
      ))}
    </div>
  );
}

/* ───────────────────────── what the person said ───────────────────────── */

function YouSaid({ result, exchange, ctx }: { result: SlotResult; exchange: readonly Exchange[]; ctx: CardContext }) {
  if (exchange.length === 0) return null;
  const decisive = turnOfQuote(exchange, result.quote);
  return (
    <div className="pc-block pc-block-said">
      <div className="kicker">You said</div>
      {exchange.map((e) => {
        const id = ctx.ids[e.index];
        if (e.turn.speaker === 'interviewer') {
          return (
            <p key={e.index} className="pc-muted pc-ask-line">
              <span className="pc-rung">{RUNG_LABEL[e.turn.rung ?? 'surface'] ?? 'Interviewer'}</span> {e.turn.text}
            </p>
          );
        }
        const pieces = markPieces(e.turn.text, decisive?.index === e.index ? result.quote?.text : undefined);
        return (
          <div key={e.index} className="pc-said-block">
            <p className="pc-said">
              “
              {pieces.map((p, i) =>
                p.mark ? (
                  <mark key={i}>{p.text}</mark>
                ) : (
                  <span key={i}>{p.text}</span>
                ),
              )}
              ”
            </p>
            <p className="pc-cite">
              <a href={`#${id}`} onClick={(ev) => jumpTo(ev, id, ctx)}>
                You · {clock(e.turn.at)}
              </a>
            </p>
          </div>
        );
      })}
      {result.quote && !decisive && (
        <p className="pc-fact">
          <span className="pc-lbl">The words this rests on </span>“<mark>{result.quote.text}</mark>” <span className="pc-cite">You · {clock(result.quote.at)}</span>
        </p>
      )}
    </div>
  );
}

/* ───────────────────────── the story, in parts ───────────────────────── */

const PARTS: readonly { key: 'situation' | 'action' | 'result' | 'reflection'; label: string }[] = [
  { key: 'situation', label: 'Situation' },
  { key: 'action', label: 'What you did' },
  { key: 'result', label: 'Result' },
  { key: 'reflection', label: "What you'd change" },
];

function StoryParts({ story }: { story: NonNullable<SlotResult['story']> }) {
  return (
    <div className="pc-block">
      <div className="kicker">The story, in parts</div>
      <ul className="pc-parts">
        {PARTS.map((p) =>
          story[p.key] ? (
            <li key={p.key} className="chip pc-part is-there">
              {p.label}
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M3 8.5l3.2 3L13 4.5" />
              </svg>
              <span className="sr-only">covered</span>
            </li>
          ) : (
            <li key={p.key} className="chip pc-part is-missing">
              {p.label} <span className="pc-muted">· missing</span>
            </li>
          ),
        )}
      </ul>
    </div>
  );
}

/* ───────────────────────── Say, Avoid, Fix ───────────────────────── */

function BuiltFrom({ sources }: { sources: readonly string[] }) {
  const items = builtFrom(sources);
  if (items.length === 0) return null;
  return (
    <p className="pc-muted pc-small">
      Built from{' '}
      {items.map((it, i) => (
        <span key={it.label}>
          {i > 0 ? (i === items.length - 1 ? ' and ' : ', ') : ''}
          {it.cite ? <span className="pc-cite">{it.label}</span> : it.label}
        </span>
      ))}
    </p>
  );
}

function Columns({ result, ctx }: { result: SlotResult; ctx: CardContext }) {
  const { say, avoid, fix } = result;
  return (
    <div className="pc-cells">
      <div className="pc-cell">
        <div className="kicker">Say</div>
        {say ? (
          <>
            <p className="pc-said pc-say">
              {sayPieces(say.text).map((p, i) =>
                p.placeholder ? (
                  <span key={i} className="pc-ph">
                    {p.text}
                  </span>
                ) : (
                  <span key={i}>{p.text}</span>
                ),
              )}
            </p>
            <BuiltFrom sources={say.sources} />
          </>
        ) : (
          <p className="pc-muted">Nothing to add</p>
        )}
      </div>
      <div className="pc-cell">
        <div className="kicker">Avoid</div>
        {avoid ? (
          <>
            <p className="pc-said">“{avoid.quote}”</p>
            <p className="pc-muted">
              <span aria-hidden="true">→ </span>
              {avoid.advice}
            </p>
          </>
        ) : (
          <p className="pc-muted">Nothing to avoid</p>
        )}
      </div>
      <div className="pc-cell">
        <div className="kicker">Fix</div>
        {fix ? (
          <>
            <p className="pc-fixtext">{fix.text}</p>
            <p className="pc-muted pc-small">About {fix.minutes} min</p>
            {ctx.fix && <FixPlace result={result} placing={ctx.fix} />}
            {!ctx.fix && ctx.onPrepare && (
              <>
                <button type="button" className="btn btn-sm pc-btn-sm pc-self-start" onClick={ctx.onPrepare}>
                  Open Prepare
                </button>
                <p className="pc-muted pc-small">Opens this role's prep page.</p>
              </>
            )}
          </>
        ) : (
          <p className="pc-muted">Nothing to fix</p>
        )}
      </div>
    </div>
  );
}

/* ───────────────────────── a Fix step, placed (I2) ───────────────────────── */

const PRACTICE = 'practice';

const planName = (p: PreparePlanSummary) => (p.requirement ? `${p.roleTitle} · ${p.requirement}` : p.roleTitle);

function FixPlace({ result, placing }: { result: SlotResult; placing: FixPlacing }) {
  const id = useId();
  const placed = placing.placed[result.slotId];
  const busy = placing.busy === result.slotId;
  const error = placing.error?.slotId === result.slotId ? placing.error.message : undefined;
  const [dest, setDest] = useState<string>(placing.defaultPlanId ?? placing.plans[0]?.id ?? PRACTICE);

  if (placed) {
    const plan = placed.planId ? placing.plans.find((p) => p.id === placed.planId) : undefined;
    return (
      <div className="pc-placed" role="status">
        <p className="pc-small">
          {placed.where === 'plan' && plan ? `Added to your plan · ${planName(plan)}` : 'Kept in Practice, on the Re-drill screen'}
          {placed.doneAt ? ` · done ${dateOf(placed.doneAt)}` : ''}
        </p>
        <div className="pc-actions">
          {plan && (
            <button type="button" className="btn btn-sm pc-btn-sm" onClick={() => placing.onOpenPlan(plan)}>
              Open the plan
            </button>
          )}
          {!plan && (
            <button type="button" className="btn btn-sm pc-btn-sm" onClick={placing.onRedrill}>
              Open Re-drill
            </button>
          )}
          <button type="button" className="btn btn-quiet btn-sm pc-btn-sm" disabled={busy} onClick={() => placing.onUndo(result.slotId)}>
            Undo
          </button>
        </div>
        {error && (
          <p className="pc-danger pc-small" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="pc-place">
      <label htmlFor={id} className="kicker">
        Where it goes
      </label>
      <select id={id} className="field pc-box pc-select" value={dest} onChange={(e) => setDest(e.target.value)}>
        {placing.plans.map((p) => (
          <option key={p.id} value={p.id}>
            Your plan · {planName(p)}
          </option>
        ))}
        <option value={PRACTICE}>Keep it in Practice</option>
      </select>
      <button type="button" className="btn btn-primary btn-sm pc-btn-sm pc-self-start" disabled={busy} onClick={() => placing.onPlace(result.slotId, dest === PRACTICE ? undefined : dest)}>
        {busy ? 'Adding…' : dest === PRACTICE ? 'Keep it' : 'Add to my plan'}
      </button>
      <p className="pc-muted pc-small">{dest === PRACTICE ? 'A short list on the Re-drill screen.' : 'It lands in the week you’re in. Ticking it changes no verdict.'}</p>
      {error && (
        <p className="pc-danger pc-small" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/* ───────────────────────── before and now, and "Or say less" (I2) ───────────────────────── */

function BeforeNow({ result, when }: { result: SlotResult; when: string }) {
  const previous = result.previous;
  if (!previous) return null;
  return (
    <p className="pc-move" aria-label={`${VERDICT_WORD[previous.verdict]} on ${dateOf(previous.on)}, ${VERDICT_WORD[result.verdict]} ${when}`}>
      <VerdictChip verdict={previous.verdict} />
      <span className="pc-muted pc-small">{dateOf(previous.on)}</span>
      <span className="pc-muted" aria-hidden="true">
        →
      </span>
      <VerdictChip verdict={result.verdict} />
      <span className="pc-muted pc-small">{when}</span>
    </p>
  );
}

function SayLess({ result }: { result: SlotResult }) {
  const [copied, setCopied] = useState(false);
  const softer = result.softer;
  if (!softer) return null;
  const plain = softer.text.replace(/^[“"]|[”"]$/g, '');
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(plain);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="pc-offer pc-sayless">
      <div className="kicker">Or say less</div>
      <p className="pc-offer-text">Twice now this line couldn’t be backed up. If it says more than you did, it’s fine to say less.</p>
      <div className="pc-offer-fact">
        <p className="pc-fact">{softer.text}</p>
        <p>
          <span className="chip pc-chip-have">From your answer</span>
        </p>
      </div>
      <div className="pc-actions">
        <button type="button" className="btn btn-sm pc-btn-sm" onClick={() => void copy()}>
          {copied ? 'Copied' : 'Copy this line'}
        </button>
      </div>
      <p className="pc-muted pc-small" role="status">
        {copied ? 'Use it in place of the line on your resume.' : 'Built only from what you said, never from the resume line.'}
      </p>
    </div>
  );
}

/* ───────────────────────── a new fact, offered ───────────────────────── */

function FactOffer({ result, ctx }: { result: SlotResult; ctx: CardContext }) {
  const [later, setLater] = useState(false);
  if (!result.newFact) return null;
  const busy = ctx.factBusy === result.slotId;
  const error = ctx.factError?.slotId === result.slotId ? ctx.factError.message : undefined;
  const at = result.quote?.at;

  if (result.factAdded) {
    return (
      <div id={`fact-${result.slotId}`} className="pc-offer is-added" tabIndex={-1}>
        <div className="kicker">Kept as a fact</div>
        <p>
          Added to your profile as a fact marked <span className="chip pc-chip-have">From you</span>
        </p>
        <div className="pc-offer-fact">
          <p className="pc-fact">“{result.newFact}”</p>
          {at !== undefined && <p className="pc-cite">You · {clock(at)}</p>}
        </div>
        <div className="pc-actions">
          <button type="button" className="btn btn-quiet btn-sm pc-btn-sm" disabled={busy} onClick={() => ctx.onFact(result.slotId, false)}>
            Remove
          </button>
        </div>
        {error && (
          <p className="pc-danger" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  }
  if (later) return null;
  return (
    <div id={`fact-${result.slotId}`} className="pc-offer" tabIndex={-1}>
      <div className="kicker">A new fact</div>
      <p className="pc-offer-text">This isn't on your resume. Add it to your profile as a fact marked From you?</p>
      <div className="pc-offer-fact">
        <p className="pc-fact">“{result.newFact}”</p>
        {at !== undefined && <p className="pc-cite">You · {clock(at)}</p>}
      </div>
      <div className="pc-actions">
        <button type="button" className="btn btn-primary btn-sm pc-btn-sm" disabled={busy} onClick={() => ctx.onFact(result.slotId, true)}>
          {busy ? 'Adding…' : 'Add as a fact · From you'}
        </button>
        <button type="button" className="btn btn-quiet btn-sm pc-btn-sm" disabled={busy} onClick={() => setLater(true)}>
          Not now
        </button>
      </div>
      {error && (
        <p className="pc-danger" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/* ───────────────────────── the body, and the card ───────────────────────── */

/** Everything under the top row of one question. */
export function CardBody({ result, ctx, titleId }: { result: SlotResult; ctx: CardContext; titleId?: string }) {
  const exchange = exchangeFor(ctx.debrief, result);
  const assessed = result.verdict !== 'not_assessed';
  const again = ctx.againBusy === result.slotId;
  const error = ctx.againError?.where === result.slotId ? ctx.againError.message : undefined;
  return (
    <>
      <h3 id={titleId} className="title pc-q">
        {result.question}
      </h3>
      <BeforeNow result={result} when={dateOf(ctx.debrief.createdAt) || 'today'} />
      <About kind={result.kind} sources={result.sources} />
      <YouSaid result={result} exchange={exchange} ctx={ctx} />
      {result.story && <StoryParts story={result.story} />}
      {(result.why || !assessed) && (
        <div className="pc-block">
          <div className="kicker">Why</div>
          <p>{result.why || 'Not asked, skipped or ended early.'}</p>
          {!assessed && <p className="pc-muted pc-small">Not assessed never counts against you.</p>}
        </div>
      )}
      {assessed && <Columns result={result} ctx={ctx} />}
      <SayLess result={result} />
      <FactOffer result={result} ctx={ctx} />
      <div className="pc-card-foot">
        <button type="button" className="btn btn-quiet btn-sm pc-btn-sm" disabled={again} onClick={() => ctx.onAgain(result.slotId)}>
          {again ? 'Starting…' : 'Ask this one again'}
        </button>
        {error && (
          <p className="pc-danger pc-small" role="alert">
            {error}
          </p>
        )}
      </div>
    </>
  );
}

/** A question as a card, on a wide screen. */
export function ResultCard({ result, index, ctx }: { result: SlotResult; index: number; ctx: CardContext }) {
  const titleId = useId();
  const exchange = exchangeFor(ctx.debrief, result);
  return (
    <article id={`q${index + 1}`} className="panel pc-qcard" aria-labelledby={titleId} tabIndex={-1}>
      <CardTop result={result} index={index} ctx={ctx} exchange={exchange} />
      <CardBody result={result} ctx={ctx} titleId={titleId} />
    </article>
  );
}

/** The question the debrief points at first, expanded on a phone. */
export function FeaturedCard({ result, index, ctx }: { result: SlotResult; index: number; ctx: CardContext }) {
  const titleId = useId();
  const exchange = exchangeFor(ctx.debrief, result);
  return (
    <article id={`q${index + 1}`} className="pc-qcard pc-qfeatured" aria-labelledby={titleId} tabIndex={-1}>
      <CardTop result={result} index={index} ctx={ctx} exchange={exchange} />
      <CardBody result={result} ctx={ctx} titleId={titleId} />
    </article>
  );
}

/** A question as a row that opens, on a phone: its number, its short name, its verdict, a chevron. */
export function ResultRow({ result, index, open, onToggle, ctx }: { result: SlotResult; index: number; open: boolean; onToggle: () => void; ctx: CardContext }) {
  const base = useId();
  const exchange = exchangeFor(ctx.debrief, result);
  return (
    <div id={`q${index + 1}`} className="pc-acc" tabIndex={-1}>
      <button type="button" className="pc-acc-row" aria-expanded={open} aria-controls={`${base}-panel`} onClick={onToggle}>
        <span className="numeral pc-muted pc-acc-n">{index + 1}</span>
        <span className="pc-acc-name">{result.topic || result.question}</span>
        <VerdictChip verdict={result.verdict} />
        <svg className={`pc-chev${open ? ' is-open' : ''}`} width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M4 6l4 4 4-4" />
        </svg>
      </button>
      {open && (
        <div id={`${base}-panel`} className="pc-acc-panel" role="region" aria-label={`Question ${index + 1}: ${result.topic || result.question}`}>
          <CardTop result={result} index={index} ctx={ctx} exchange={exchange} />
          <CardBody result={result} ctx={ctx} />
        </div>
      )}
    </div>
  );
}

