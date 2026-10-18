/**
 * Before you start, drawn (board `Setup` of the Interview canvas): the plan the interviewer will follow, with a way to skip
 * any question, what to spend more time on, how long, how you will answer, and how it works. Beside it, what this practice
 * is, the free line, and Start.
 *
 * Presentational: `SetupScreen` loads the plan and starts the interview; the dev gallery draws this from fixtures. The plan
 * is shown here and not during the interview, which is why each source is quoted word for word with where it is from.
 *
 * A role of the person's graph has no posting: its gaps cite "The role" (what the role needs, as the graph reads it), and
 * the side summary counts its requirements with the same pips a posting's asks get. No percentage, here or anywhere.
 */
import { useId } from 'react';
import type { ReactNode } from 'react';
import type { AiJob } from '../../../shared/jobsearch';
import type { Role } from '../../../shared/types';
import '../../styles/practice-room.css';
import type { InterviewFocus, InterviewKind, InterviewLength, InterviewPlan, PlanSlot, SlotSource } from '../../../shared/interview';
import { asksLabel, asksLine, asksTotal, cleanAsks, FIT_WORD, LEVEL_WORD, placeLine } from '../../discover/prepare/words';
import { MicIcon } from '../room/icons';
import { MicMeter } from '../room/MicMeter';
import type { MeterTrouble, Prefs, VoiceSetting } from '../speech';
import {
  FREE_LINE,
  GRAPH_ROLE_BLURB,
  UNLIMITED_LINE,
  HOW_IT_ENDS,
  HOW_IT_WORKS,
  KIND_BLURB,
  KIND_LABEL,
  LIMIT_USED,
  MIC_BLOCKED,
  NO_SPEECH,
  NO_VOICE,
  SLOT_KIND_LABEL,
  VERDICT_LABEL,
  VERDICT_ORDER,
  engineNote,
  privacyLine,
  questionsWord,
  quoted,
  requirementStanding,
  requirementsLine,
} from '../words';
import type { RequirementStanding, SpeechBy } from '../words';


export type PlanProblem = { message: string; noTemplate: boolean; gone: boolean };

export interface MicTest {
  testing: boolean;
  /** The meter heard a voice. */
  heard: boolean;
  /** The test could not run: the browser (or the person) said no, or the microphone could not be had. */
  problem?: MeterTrouble;
}

export interface SetupViewProps {
  kind: InterviewKind;
  /** The sample profile: practice runs, but on someone else's work. */
  sample: boolean;
  title: string;
  /** The line under the title. */
  sub: string;
  /** The role, when it is a role the person tracks or found. */
  job?: AiJob;
  /** The role, when it is one of the person's graph (no posting). */
  graphRole?: Role;
  plan: InterviewPlan | null;
  loading: boolean;
  problem?: PlanProblem;
  /** The question counts and lengths of both options, for the length cards. */
  lengths: Partial<Record<InterviewLength, InterviewPlan>>;
  length: InterviewLength;
  focus?: InterviewFocus;
  skipped: ReadonlySet<string>;
  prefs: Prefs;
  speechSupported: boolean;
  voiceSupported: boolean;
  /** Who will write the words down (Sarvam AI when the server offers it, else the browser): the privacy copy says so. */
  listenBy: SpeechBy;
  mic: MicTest;
  /** Practice interviews left today, when the server says. */
  left?: number;
  /** The account has no limits: no daily count and no "used up" state, whatever `left` says. */
  unlimited?: boolean;
  starting: boolean;
  startError?: string;
  onBack(): void;
  onSkip(id: string): void;
  onFocus(focus: InterviewFocus | undefined): void;
  onLength(length: InterviewLength): void;
  onAnswer(answer: Prefs['answer']): void;
  onVoice(voice: VoiceSetting): void;
  onTestMic(): void;
  onMicLevel(peak: number): void;
  onMicBlocked(why: MeterTrouble): void;
  onStart(): void;
  onRetry(): void;
  onOtherKind(): void;
  onAnalyse(): void;
  onAddToProfile(): void;
}

/** What the person sees of a source: its words in quotes, with where they are from. */
function sourceRow(slot: PlanSlot, s: SlotSource, role?: Role): { cite: string; fact: string } {
  if (s.origin === 'role') {
    const found = role?.requirements.find((r) => r.name.trim() === s.text);
    const lead = found && requirementStanding(found) === 'below' ? "Below the role's level: " : 'Not shown yet: ';
    return { cite: 'The role', fact: `${lead}${quoted(s.text)}` };
  }
  const cite = s.origin === 'posting' ? 'Posting' : s.source || 'Your evidence';
  if (slot.kind === 'gap') return { cite, fact: `${s.origin === 'posting' ? 'Not on your resume: ' : 'Nearest evidence: '}${quoted(s.text)}` };
  return { cite, fact: quoted(s.text) };
}

const FOCUS_CHOICES: ReadonlyArray<{ value: InterviewFocus | undefined; label: string }> = [
  { value: 'gaps', label: 'My gaps' },
  { value: 'claims', label: 'My strongest claims' },
  { value: 'stories', label: 'Stories' },
  { value: undefined, label: 'Keep the plan as it is' },
];

const VOICES: ReadonlyArray<{ value: VoiceSetting; label: string }> = [
  { value: 'normal', label: 'On, normal speed' },
  { value: 'slower', label: 'On, slower' },
  { value: 'off', label: 'Off, text only' },
];

export function SetupView(p: SetupViewProps) {
  const ids = useId();
  const kept = p.plan ? p.plan.slots.filter((s) => !p.skipped.has(s.id)) : [];
  const answerValue = p.speechSupported ? p.prefs.answer : 'type';
  const employer = p.job?.employer ?? (p.plan?.title.includes(' at ') ? p.plan.title.slice(p.plan.title.lastIndexOf(' at ') + 4) : '');
  const left = p.unlimited ? undefined : p.left;
  const out = left === 0;
  const canStart = !!p.plan && kept.length > 0 && !p.starting && !out && !p.loading;

  const lede = (() => {
    if (!p.plan) return '';
    const n = kept.length;
    const wontShow = "The interviewer won't show you this during the interview.";
    if (p.kind === 'role' && p.graphRole) return `${questionsWord(n)} from what the role needs and what your evidence shows. ${wontShow}`;
    if (p.kind === 'role') return `${questionsWord(n)} from what ${employer || 'the role'} asks and what your resume shows. ${wontShow}`;
    if (p.kind === 'work') return `A small real task, then ${n === 1 ? 'one question' : `${questionsWord(n).toLowerCase()}`} on your choices. The questions are about what you actually write.`;
    return `${questionsWord(n)} from your own evidence. ${wontShow}`;
  })();

  return (
    <div className="rm-page rm-setup" aria-busy={p.loading}>
      <header className="rm-head">
        <button type="button" className="rm-back" onClick={p.onBack}>
          <span aria-hidden="true">←</span> Practice
        </button>
        <p className="kicker">Practice · {KIND_LABEL[p.kind]}</p>
        <h1 className="serif rm-title">{p.title}</h1>
        <p className="rm-muted">{p.sub}</p>
      </header>

      {p.sample && (
        <div className="rm-shown rm-notice" role="note">
          <p>This is the sample profile&apos;s interview. Analyse your own resume to practise on your own work.</p>
          <div className="rm-buttons">
            <button type="button" className="btn btn-primary rm-btn" onClick={p.onAnalyse}>
              Analyse my resume
            </button>
          </div>
        </div>
      )}

      <div className="rm-setup-cols">
        <div className="rm-setup-main">
          {/* the plan */}
          <section className="panel rm-sec" aria-labelledby={`${ids}-plan`}>
            <h2 id={`${ids}-plan`} className="serif rm-h2">
              The plan
            </h2>

            {p.problem ? (
              <div className="rm-shown rm-notice" role="alert">
                <p>{p.problem.message}</p>
                <div className="rm-buttons">
                  {p.problem.noTemplate ? (
                    <button type="button" className="btn btn-primary rm-btn" onClick={p.onOtherKind}>
                      Practise This role
                    </button>
                  ) : p.problem.gone ? (
                    <button type="button" className="btn btn-primary rm-btn" onClick={p.onBack}>
                      Back to Practice
                    </button>
                  ) : (
                    <button type="button" className="btn btn-primary rm-btn" onClick={p.onRetry}>
                      Try again
                    </button>
                  )}
                </div>
              </div>
            ) : !p.plan ? (
              <p className="rm-muted" role="status">
                Working out your questions…
              </p>
            ) : (
              <>
                <p className="rm-muted rm-lede">{lede}</p>
                {p.kind === 'role' && <p className="rm-faint rm-small">{p.graphRole ? 'Your biggest gaps come first.' : 'Asks the posting marks as must-have come first.'}</p>}
                {p.plan.note && (
                  <div className="rm-shown" role="note">
                    <p>{p.plan.note}</p>
                    {/* only a short profile can be helped by adding to it; any other note (a posting's text aimed at an AI) is only said */}
                    {/profile is short/i.test(p.plan.note) && (
                      <div className="rm-buttons">
                        <button type="button" className="btn btn-primary rm-btn" onClick={p.onAddToProfile}>
                          Add to my profile
                        </button>
                      </div>
                    )}
                  </div>
                )}
                {p.plan.slots.length === 0 && <p className="rm-muted">Your profile has nothing to ask about yet. Add a project or a link, then come back.</p>}
                <ol className="rm-slots">
                  {p.plan.slots.map((slot, i) => {
                    const skipped = p.skipped.has(slot.id);
                    const label = skipped ? 'Put it back' : 'Skip this one';
                    return (
                      <li key={slot.id} className="rm-slot">
                        <span className="numeral rm-slot-n">{i + 1}</span>
                        <div className={`rm-slot-body${skipped ? ' is-skipped' : ''}`}>
                          <div className="rm-slot-head">
                            <span className="rm-slot-name">{slot.topic || SLOT_KIND_LABEL[slot.kind]}</span>
                            <span className={`chip rm-kind rm-kind-${slot.kind}`}>{SLOT_KIND_LABEL[slot.kind]}</span>
                          </div>
                          {slot.sources.map((s, j) => {
                            const row = sourceRow(slot, s, p.graphRole);
                            return (
                              <div className="rm-slot-source" key={j}>
                                <span className="rm-cite">{row.cite}</span>
                                <span className="rm-fact">{row.fact}</span>
                              </div>
                            );
                          })}
                        </div>
                        {p.kind !== 'work' && (
                          <button type="button" className="btn btn-quiet btn-sm rm-btn-sm" aria-label={`${label}: ${slot.topic || SLOT_KIND_LABEL[slot.kind]}`} onClick={() => p.onSkip(slot.id)}>
                            {label}
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ol>
                {kept.length === 0 && p.plan.slots.length > 0 && <p className="rm-muted">Keep at least one question.</p>}
              </>
            )}
          </section>

          {/* spend more time on */}
          {p.kind === 'role' && (
            <section className="panel rm-sec" aria-labelledby={`${ids}-focus`}>
              <h2 id={`${ids}-focus`} className="serif rm-h2 rm-h2-sm">
                Spend more time on
              </h2>
              <div className="rm-choices">
                {FOCUS_CHOICES.map((c) => (
                  <button key={c.label} type="button" className="rm-choice" aria-pressed={p.focus === c.value} onClick={() => p.onFocus(c.value)}>
                    {c.label}
                  </button>
                ))}
              </div>
            </section>
          )}

          {/* length */}
          {p.kind !== 'work' && (
            <section className="panel rm-sec" aria-labelledby={`${ids}-length`}>
              <h2 id={`${ids}-length`} className="serif rm-h2 rm-h2-sm">
                Length
              </h2>
              <div className="rm-cards">
                {(['short', 'full'] as const).map((value) => {
                  const plan = p.lengths[value];
                  return (
                    <label key={value} className={`rm-rcard${p.length === value ? ' is-on' : ''}`}>
                      <input type="radio" name={`${ids}-length`} value={value} checked={p.length === value} onChange={() => p.onLength(value)} />
                      <span className="rm-rcard-text">
                        <span className="rm-strong">{value === 'short' ? 'Short' : 'Full'}</span>
                        <span className="rm-muted rm-small">{plan ? `${plan.slots.length} ${plan.slots.length === 1 ? 'question' : 'questions'} · about ${plan.minutes} min` : '…'}</span>
                      </span>
                    </label>
                  );
                })}
              </div>
            </section>
          )}

          {/* how you'll answer */}
          <section className="panel rm-sec" aria-labelledby={`${ids}-answer`}>
            <h2 id={`${ids}-answer`} className="serif rm-h2 rm-h2-sm">
              How you&apos;ll answer
            </h2>
            <div className="rm-cards">
              <label className={`rm-rcard${answerValue === 'speech' ? ' is-on' : ''}${p.speechSupported ? '' : ' is-off'}`}>
                <input type="radio" name={`${ids}-mode`} value="speech" checked={answerValue === 'speech'} disabled={!p.speechSupported} onChange={() => p.onAnswer('speech')} />
                <span className="rm-rcard-text">
                  <span className="rm-strong">Speak</span>
                  <span className="rm-muted rm-small">the interviewer listens and writes down what you say</span>
                </span>
              </label>
              <label className={`rm-rcard${answerValue === 'type' ? ' is-on' : ''}`}>
                <input type="radio" name={`${ids}-mode`} value="type" checked={answerValue === 'type'} onChange={() => p.onAnswer('type')} />
                <span className="rm-rcard-text">
                  <span className="rm-strong">Type</span>
                  <span className="rm-muted rm-small">for a quiet library or a shared room</span>
                </span>
              </label>
            </div>

            {!p.speechSupported && (
              <div className="rm-shown" role="note">
                <p>{NO_SPEECH}</p>
              </div>
            )}

            {answerValue === 'speech' && p.speechSupported && (
              <div className="rm-mictest">
                <button type="button" className="btn rm-btn" aria-pressed={p.mic.testing} onClick={p.onTestMic}>
                  <MicIcon />
                  {p.mic.testing ? 'Stop the test' : 'Test my microphone'}
                </button>
                <MicMeter active={p.mic.testing} onLevel={p.onMicLevel} onBlocked={p.onMicBlocked} />
                <span className="rm-muted rm-mictext" role="status" aria-live="polite">
                  {p.mic.problem === 'denied'
                    ? `${MIC_BLOCKED.title} Your browser blocked the microphone. Allow it from the address bar, or choose Type.`
                    : p.mic.problem === 'busy'
                      ? "We couldn't open your microphone. Another app or tab may be using it. You can still choose Type."
                      : p.mic.testing && p.mic.heard
                      ? 'We heard you. The meter moves when you speak.'
                      : p.mic.testing
                        ? 'Say something. The meter moves when you speak.'
                        : 'The meter moves when you speak.'}
                </span>
              </div>
            )}

            <div className="rm-field">
              <label htmlFor={`${ids}-voice`} className="kicker">
                Interviewer&apos;s voice
              </label>
              <select
                id={`${ids}-voice`}
                className="rm-select"
                value={p.voiceSupported ? p.prefs.voice : 'off'}
                disabled={!p.voiceSupported}
                onChange={(e) => p.onVoice(e.target.value as VoiceSetting)}
              >
                {VOICES.map((v) => (
                  <option key={v.value} value={v.value}>
                    {v.label}
                  </option>
                ))}
              </select>
              {!p.voiceSupported && <span className="rm-faint rm-small">{NO_VOICE}</span>}
            </div>

            <div className="rm-privacy">
              <p className="rm-muted rm-small rm-measure">{privacyLine(p.listenBy)}</p>
              {answerValue === 'speech' && p.speechSupported && <p className="rm-faint rm-small">{engineNote(p.listenBy)}</p>}
            </div>
          </section>

          {/* how it works */}
          <section className="panel rm-sec" aria-labelledby={`${ids}-how`}>
            <h2 id={`${ids}-how`} className="serif rm-h2 rm-h2-sm">
              How it works
            </h2>
            <ul className="rm-how">
              {HOW_IT_WORKS.map((line) => (
                <li key={line}>{line}</li>
              ))}
              <li>
                <p>{HOW_IT_ENDS}</p>
                <div className="rm-verdicts">
                  {VERDICT_ORDER.map((v) => (
                    <span key={v} className={`rm-v rm-v-${v}`}>
                      {VERDICT_LABEL[v]}
                    </span>
                  ))}
                </div>
              </li>
            </ul>
          </section>
        </div>

        <aside className="rm-setup-side" aria-label="About this practice">
          <section className="panel rm-summary" aria-label={p.job || p.graphRole ? 'This role' : 'This practice'}>
            {p.job ? <RoleSummary job={p.job} /> : p.graphRole ? <GraphRoleSummary role={p.graphRole} /> : <KindSummary kind={p.kind} plan={p.plan} count={kept.length} />}

            <div className="rm-summary-foot">
              <p className="rm-faint rm-small">{p.unlimited ? UNLIMITED_LINE : left !== undefined ? `${FREE_LINE} · ${left} left today` : FREE_LINE}</p>
              {out && <p className="rm-muted rm-small">{LIMIT_USED}</p>}
            </div>

            {p.startError && (
              <p className="rm-danger rm-small" role="alert">
                {p.startError}
              </p>
            )}
            <div className="rm-side-actions">
              <button type="button" className="btn btn-primary rm-btn rm-wide" disabled={!canStart} onClick={p.onStart}>
                {p.starting ? 'Starting…' : 'Start the interview'}
              </button>
              {p.starting && (
                <p className="rm-muted rm-small" role="status">
                  Writing your questions. It takes a few seconds.
                </p>
              )}
              <button type="button" className="btn btn-quiet rm-btn rm-wide" onClick={p.onBack} disabled={p.starting}>
                Practise a different kind
              </button>
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

/** One square per ask the search checked, in order: met, partly, not on the resume. More than 30 add nothing a count does not say. */
function pipKinds(asks: { met: number; partly: number; notShown: number }): Array<'met' | 'part' | 'gap'> {
  const many = (n: number, kind: 'met' | 'part' | 'gap') => Array.from({ length: Math.min(n, 30) }, () => kind);
  return [...many(asks.met, 'met'), ...many(asks.partly, 'part'), ...many(asks.notShown, 'gap')].slice(0, 30);
}

function RoleSummary({ job }: { job: AiJob }) {
  const asks = cleanAsks(job.asks);
  const total = asksTotal(asks);
  return (
    <>
      <p className="kicker">This role</p>
      <div className="rm-summary-fit">
        <span className={`rm-fit rm-fit-${job.fit}`}>{FIT_WORD[job.fit]}</span>
        <span className="rm-muted rm-small">{LEVEL_WORD[job.level]}</span>
      </div>
      <div>
        <p className="rm-strong">{job.employer}</p>
        <p className="rm-muted rm-small">{placeLine(job)}</p>
      </div>
      {total > 0 && (
        <div className="rm-askpips">
          <span className="rm-pips" role="img" aria-label={asksLabel(asks)}>
            {pipKinds(asks).map((k, i) => (
              <span key={i} className={`rm-pip rm-pip-${k}`} aria-hidden="true" />
            ))}
          </span>
          <span className="rm-muted rm-small">{asksLine(asks)}</span>
        </div>
      )}
    </>
  );
}

const PIP_OF: Record<RequirementStanding, 'met' | 'part' | 'gap'> = { met: 'met', below: 'part', notShown: 'gap' };

/** A role of the person's graph: its title, and its requirements as pips (met, below the role's level, not shown yet). */
function GraphRoleSummary({ role }: { role: Role }) {
  const order: readonly RequirementStanding[] = ['met', 'below', 'notShown'];
  const pips = order.flatMap((s) => role.requirements.filter((r) => requirementStanding(r) === s).map(() => PIP_OF[s])).slice(0, 30);
  const line = requirementsLine(role.requirements);
  return (
    <>
      <p className="kicker">This role</p>
      <p className="rm-strong">{role.title}</p>
      <p className="rm-muted rm-small">{GRAPH_ROLE_BLURB}</p>
      {pips.length > 0 && (
        <div className="rm-askpips">
          <span className="rm-pips" role="img" aria-label={line}>
            {pips.map((k, i) => (
              <span key={i} className={`rm-pip rm-pip-${k}`} aria-hidden="true" />
            ))}
          </span>
          <span className="rm-muted rm-small">{line}</span>
        </div>
      )}
    </>
  );
}

function KindSummary({ kind, plan, count }: { kind: InterviewKind; plan: InterviewPlan | null; count: number }): ReactNode {
  return (
    <>
      <p className="kicker">{KIND_LABEL[kind]}</p>
      <p className="rm-strong">{plan ? plan.title : KIND_LABEL[kind]}</p>
      <p className="rm-muted rm-small">{KIND_BLURB[kind]}</p>
      {plan && plan.slots.length > 0 && (
        <p className="rm-muted rm-small">{`${count} ${count === 1 ? 'question' : 'questions'} · about ${Math.max(1, Math.round((plan.minutes * count) / plan.slots.length))} min`}</p>
      )}
    </>
  );
}
