/**
 * The five sections of the prepare page, in the order the board draws them: what to lead with, the gaps, the likely
 * questions with their stories, the checklist, and the interview practice that is coming next.
 *
 * What is quoted (an ask from the posting, a line of the person's evidence) is shown word for word with its source.
 * What is ours (a step, a hint) wears the dashed "Suggestion" chip. A dashed box is for what only the person knows: it is
 * a real textarea, kept in this browser per role, and nothing in it is ever sent anywhere or written for them.
 */
import { useId } from 'react';
import type { ReactNode } from 'react';
import type { AiJob, AskCounts, PrepGap, PrepLead, PrepQuestion, StoryPart } from '../../../shared/jobsearch';
import { useFreeLine } from '../../practice/useFreeLine';
import { actions, useApp } from '../../store';
import '../../styles/discover-more.css';
import { Evidence, Suggest, quoted } from './parts';
import { MAX_STORY_CHARS } from './storage';
import { asksTotal, dayLabel, hostFor } from './words';

interface Slots {
  /** What the person wrote in the dashed box `slot`, if anything. */
  get: (slot: string) => string;
  set: (slot: string, value: string) => void;
}

const sectionHead = (kicker: string, headingId: string, title: string, lede?: string) => (
  <header className="dm-sec-head">
    <p className="kicker">{kicker}</p>
    <h2 className="serif dm-h2" id={headingId} tabIndex={-1}>
      {title}
    </h2>
    {lede && <p className="dm-muted dm-lede">{lede}</p>}
  </header>
);

/* ───────────────────────── lead with these ───────────────────────── */

function leadLede(lead: readonly PrepLead[], asks: AskCounts | undefined, hasGaps: boolean): string {
  const n = lead.length;
  if (n === 0) return hasGaps ? 'None of the asks we checked are on your profile yet. Start with the gap below.' : 'None of the asks we checked are on your profile yet.';
  const total = asksTotal(asks);
  const partly = lead.filter((l) => l.partly).length;
  const count =
    total >= n
      ? `${n} of the ${total} asks we checked ${n === 1 ? 'is' : 'are'} on your resume${partly > 0 ? `, ${partly} only partly` : ''}.`
      : `${n === 1 ? 'This ask is' : 'These asks are'} on your resume${partly > 0 ? `, ${partly} only partly` : ''}.`;
  return `${count} Put ${n === 1 ? 'it' : 'them'} first, in your resume, your cover note and your first answers.`;
}

export function LeadSection({ headingId, lead, hasGaps, job, onSeeMatch }: { headingId: string; lead: readonly PrepLead[]; hasGaps: boolean; job?: AiJob; onSeeMatch?: () => void }) {
  return (
    <section className="panel dm-sec" aria-labelledby={headingId}>
      {sectionHead('Your strongest ground', headingId, 'Lead with these', leadLede(lead, job?.asks, hasGaps))}
      {lead.map((l, i) => (
        <div className="dm-pair" key={i}>
          <div className="dm-pair-cols">
            <div className="dm-pair-col">
              <p className="kicker">They ask</p>
              <p className="dm-fact">{quoted(l.ask)}</p>
            </div>
            <div className="dm-pair-col">
              <p className="kicker">You show</p>
              <Evidence cite={l.cite} />
              {l.partly && <span className="chip dm-chip-partly">Partly met</span>}
            </div>
          </div>
        </div>
      ))}
      {lead.length > 0 && <Suggest>Open your cover note with the first of these, so the strongest ask is answered before they have to look for it.</Suggest>}
      {onSeeMatch && (
        <div className="dm-seematch">
          <p className="dm-muted dm-small">The full match lists every requirement against your evidence.</p>
          <button type="button" className="btn btn-sm dm-btn-sm" onClick={onSeeMatch}>
            See how you fit
          </button>
        </div>
      )}
    </section>
  );
}

/* ───────────────────────── close or explain the gap ───────────────────────── */

function GapBlock({ gap, index, slots }: { gap: PrepGap; index: number; slots: Slots }) {
  const id = useId();
  const slot = `g${index}`;
  return (
    <article className="dm-gapitem" aria-label={`Gap ${index + 1}`}>
      <div className="dm-gapline">
        <span className="dm-pip dm-pip-gap" aria-hidden="true" />
        <p className="dm-gaptext">Not on your resume: {quoted(gap.gap)}</p>
      </div>

      <div className="dm-block">
        <h3 className="dm-h3">What you can show now</h3>
        {gap.nearest ? (
          <>
            <div className="dm-evidence">
              <p className="dm-fact">
                <span className="dm-lbl">You show</span> {quoted(gap.nearest.text)}
              </p>
              {gap.nearest.source && <p className="dm-cite">{gap.nearest.source}</p>}
            </div>
            <p className="dm-muted dm-small">This is the nearest evidence on your profile. It does not show the ask above.</p>
          </>
        ) : (
          <p className="dm-muted">Nothing on your profile is close to this ask yet.</p>
        )}
      </div>

      {gap.step && (
        <div className="dm-block">
          <h3 className="dm-h3">A small step before you apply</h3>
          <Suggest>{gap.step}</Suggest>
          <div className="dm-slotfield">
            <label htmlFor={id} className="kicker">
              Your line
            </label>
            <textarea
              id={id}
              className="field dm-box dm-slot"
              rows={3}
              maxLength={MAX_STORY_CHARS}
              placeholder="[add: your answer to this gap, in a line or two]"
              value={slots.get(slot)}
              onChange={(e) => slots.set(slot, e.target.value)}
            />
          </div>
          <p className="dm-muted dm-small">Only you know this line. We do not write it for you.</p>
        </div>
      )}
    </article>
  );
}

export function GapSection({ headingId, gaps, slots }: { headingId: string; gaps: readonly PrepGap[]; slots: Slots }) {
  const lede =
    gaps.length === 0
      ? 'No gaps came up in the asks we checked.'
      : `${gaps.length === 1 ? 'One ask is' : `${gaps.length} asks are`} not on your resume. Close ${gaps.length === 1 ? 'it' : 'them'} with something real, or explain ${gaps.length === 1 ? 'it' : 'each'} in one plain line.`;
  const hasNearest = gaps.some((g) => g.nearest);
  return (
    <section className="panel dm-sec" aria-labelledby={headingId}>
      {sectionHead('The gap', headingId, 'Close or explain the gap', lede)}
      {gaps.map((g, i) => (
        <GapBlock key={i} gap={g} index={i} slots={slots} />
      ))}
      {gaps.length > 0 && (
        <div className="dm-block">
          <h3 className="dm-h3">If you cannot close {gaps.length === 1 ? 'it' : 'them'} before you apply</h3>
          <Suggest>
            {hasNearest
              ? 'Name the gap in one plain line of your cover note, and point to the nearest work shown above.'
              : 'Name the gap in one plain line of your cover note, and say what you are doing about it.'}
          </Suggest>
        </div>
      )}
    </section>
  );
}

/* ───────────────────────── questions they are likely to ask ───────────────────────── */

function Cell({ part, slot, questionTitleId, slots }: { part: StoryPart; slot: string; questionTitleId: string; slots: Slots }) {
  const id = useId();
  return (
    <div className="dm-cell">
      {part.placeholder ? (
        <label htmlFor={id} className="kicker">
          {part.label}
        </label>
      ) : (
        <p className="kicker">{part.label}</p>
      )}
      {part.cite && <Evidence cite={part.cite} />}
      {part.placeholder && (
        <textarea
          id={id}
          className="field dm-box dm-slot"
          rows={3}
          maxLength={MAX_STORY_CHARS}
          placeholder={part.placeholder}
          aria-describedby={questionTitleId}
          value={slots.get(slot)}
          onChange={(e) => slots.set(slot, e.target.value)}
        />
      )}
    </div>
  );
}

function QuestionBlock({ q, index, notOnResume, slots }: { q: PrepQuestion; index: number; notOnResume: boolean; slots: Slots }) {
  const titleId = useId();
  return (
    <article className="dm-question" aria-labelledby={titleId}>
      <header className="dm-question-head">
        <p className="kicker">Question {index + 1}</p>
        <h3 className="serif dm-h3s" id={titleId}>
          {q.question}
        </h3>
        <p className="dm-muted dm-small">They ask: {quoted(q.ask)}</p>
        {notOnResume && <p className="dm-muted dm-small">This ask is not on your resume yet, so expect it. Start from the line you write under the gap.</p>}
      </header>
      {q.story.length > 0 && (
        <div className="dm-story">
          {q.story.map((part, i) => (
            <Cell key={i} part={part} slot={`q${index}.${i}`} questionTitleId={titleId} slots={slots} />
          ))}
        </div>
      )}
    </article>
  );
}

export function QuestionsSection({ headingId, questions, gaps, slots }: { headingId: string; questions: readonly PrepQuestion[]; gaps: readonly PrepGap[]; slots: Slots }) {
  const gapAsks = new Set(gaps.map((g) => g.gap));
  const lede =
    questions.length === 0
      ? 'This posting gave us no likely questions to prepare.'
      : `${questions.length === 1 ? 'One question' : `${questions.length} questions`} this posting is likely to lead to, each tied to something it asks. The questions are our suggestions. Lines with a source are quoted from your profile.`;
  return (
    <section className="panel dm-sec" aria-labelledby={headingId}>
      {sectionHead('Your stories', headingId, 'Questions they are likely to ask', lede)}
      {questions.length > 0 && (
        <p className="dm-slotkey">
          <span className="dm-slotkey-box" aria-hidden="true" />
          <span className="dm-muted dm-small">A dashed box is for what only you know. We do not guess it.</span>
        </p>
      )}
      {questions.map((q, i) => (
        <QuestionBlock key={i} q={q} index={i} notOnResume={gapAsks.has(q.ask)} slots={slots} />
      ))}
    </section>
  );
}

/* ───────────────────────── before you apply ───────────────────────── */

export interface ChecklistProps {
  headingId: string;
  job?: AiJob;
  /** The ticks the person made on this page. */
  ticks: Record<string, true>;
  onTick: (item: 'fit' | 'resume' | 'cover' | 'apply', on: boolean) => void;
  /** Open the match for this role; absent when the role is not known. */
  onSeeMatch?: () => void;
  applyHref?: string;
  /** Where the role stands in Your applications. */
  stage?: 'saved' | 'applied' | 'interviewing' | 'offer' | 'closed';
  appliedAt?: string;
  onApplied: (on: boolean) => void;
}

function Step({ id, checked, disabled, onChange, title, note, children }: { id: string; checked: boolean; disabled?: boolean; onChange: (on: boolean) => void; title: string; note: string; children?: ReactNode }) {
  return (
    <li className="dm-step">
      <input id={id} type="checkbox" className="dm-check" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <label htmlFor={id} className="dm-step-label">
        <span className="dm-step-title">{title}</span>
        <span className="dm-muted dm-small">{note}</span>
      </label>
      {children}
    </li>
  );
}

export function ChecklistSection({ headingId, job, ticks, onTick, onSeeMatch, applyHref, stage, appliedAt, onApplied }: ChecklistProps) {
  const base = useId();
  const host = job ? hostFor(job) : undefined;
  const moved = stage === 'applied' || stage === 'interviewing' || stage === 'offer';
  const appliedNote =
    stage === 'applied'
      ? appliedAt
        ? `In Applied since ${dayLabel(appliedAt)}. Untick to move it back to Saved.`
        : 'In Applied in Your applications. Untick to move it back to Saved.'
      : stage === 'interviewing' || stage === 'offer'
        ? `Already in ${stage === 'offer' ? 'Offer' : 'Interviewing'} in Your applications. Change the stage there.`
        : "Moves this role to Applied in Your applications, with today's date.";
  return (
    <section className="panel dm-sec" aria-labelledby={headingId}>
      {sectionHead('Checklist', headingId, 'Before you apply')}
      <ul className="dm-steps">
        <Step id={`${base}-fit`} checked={ticks.fit === true} onChange={(on) => onTick('fit', on)} title="See how you fit" note="Every requirement against your evidence.">
          {onSeeMatch && (
            <button type="button" className="btn btn-sm dm-btn-sm" onClick={onSeeMatch}>
              Open
            </button>
          )}
        </Step>
        <Step id={`${base}-resume`} checked={ticks.resume === true} onChange={(on) => onTick('resume', on)} title="Tailored resume" note="Built in the match, only from facts it can cite.">
          {onSeeMatch && (
            <button type="button" className="btn btn-sm dm-btn-sm" onClick={onSeeMatch}>
              Open
            </button>
          )}
        </Step>
        <Step id={`${base}-cover`} checked={ticks.cover === true} onChange={(on) => onTick('cover', on)} title="Cover note" note="Also built in the match, from the same cited facts.">
          {onSeeMatch && (
            <button type="button" className="btn btn-sm dm-btn-sm" onClick={onSeeMatch}>
              Open
            </button>
          )}
        </Step>
        <Step id={`${base}-apply`} checked={ticks.apply === true} onChange={(on) => onTick('apply', on)} title={host ? `Apply on ${host} ↗` : 'Apply on the employer’s board ↗'} note="Opens the employer's own board in a new tab.">
          {applyHref && host && (
            <a className="btn btn-primary btn-sm dm-btn-sm" href={applyHref} target="_blank" rel="noopener noreferrer" aria-label={`Open ${host}, in a new tab`}>
              {`Open ${host} ↗`}
            </a>
          )}
        </Step>
        <Step
          id={`${base}-applied`}
          checked={moved}
          disabled={!job || stage === 'interviewing' || stage === 'offer'}
          onChange={onApplied}
          title="Mark as applied"
          note={job ? appliedNote : 'Open this role from the list to track it.'}
        />
      </ul>
    </section>
  );
}

/* ───────────────────────── practice the interview ───────────────────────── */

export interface PracticeSectionProps {
  headingId: string;
  /** The search this role came from and the role; absent = the ones the Discover address names (the prep page is open on them). */
  runId?: string;
  jobId?: string;
  /** Where "Practise for this role" goes; absent = the plan screen of Practice, for this role. */
  onPractise?: (role: { runId: string; jobId: string }) => void;
}

export function PracticeSection({ headingId, runId, jobId, onPractise }: PracticeSectionProps) {
  // the prep page is open on a role of a stored search, and the address says which
  const lens = useApp((s) => s.discoverRoute);
  const freeLine = useFreeLine();
  const run = runId ?? lens.run;
  const job = jobId ?? lens.job;
  const go = () => {
    if (!run || !job) return;
    if (onPractise) onPractise({ runId: run, jobId: job });
    else actions.openPractice({ screen: 'setup', kind: 'role', run, job });
  };
  return (
    <section className="panel dm-sec dm-sec-quiet" aria-labelledby={headingId}>
      <div className="dm-practice-text">
        <div className="dm-practice-title">
          <h2 className="serif dm-h2s" id={headingId} tabIndex={-1}>
            Practice the interview
          </h2>
        </div>
        <p className="dm-muted">A mock interview on this posting, grounded in your own work. Credit only for what you can back up.</p>
        <p className="dm-muted dm-small">{freeLine}</p>
      </div>
      <button type="button" className="btn dm-btn" disabled={!run || !job} onClick={go}>
        Practise for this role
      </button>
    </section>
  );
}
