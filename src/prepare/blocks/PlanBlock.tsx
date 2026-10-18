/**
 * Your plan: the move's phases with the weeks computed from the hours a week the person picks (3, 6 or 10), one
 * phase open at a time, its tasks as real checkboxes kept with the plan, what they'll have at the end, and a link to
 * what they shipped. Wide: phase pills, then the open phase. Narrow (a phone): a list where the open phase expands
 * in place, as the phone board shows.
 */
import { useId, useState } from 'react';
import type { FormEvent } from 'react';
import type { PrepareLink, PrepareMove, PreparePhase, PrepareState } from '../../../shared/prepare';
import { hostPath, hourChoices, moveProgress, phaseProgress, phaseSpans, shortDate, taskKey, totalHours, totalWeeks, webUrl } from '../plan';

interface PlanProps {
  move: PrepareMove;
  state: PrepareState;
  narrow: boolean;
  phaseIndex: number;
  onPhase: (i: number) => void;
  onHours: (h: number) => void;
  onTask: (key: string, done: boolean) => void;
  onLink: (phaseIndex: number, url: string) => void;
}

export function PlanBlock({ move, state, narrow, phaseIndex, onPhase, onHours, onTask, onLink }: PlanProps) {
  const h = state.hoursPerWeek;
  const spans = phaseSpans(move.phases, h);
  const progress = moveProgress(move, state.done);
  const cur = Math.min(phaseIndex, move.phases.length - 1);
  const summary = `${totalWeeks(move, h)} weeks · ${totalHours(move)} hours in all · ${progress.done} of ${progress.all} done`;

  return (
    <section id="pp-plan" className="panel pp-block pp-plan" aria-labelledby="pp-plan-h">
      <div className="pp-head pp-plan-head">
        <h2 id="pp-plan-h" className="serif pp-plan-title">
          Your plan
        </h2>
        {narrow && <span className="numeral pp-meta">{summary}</span>}
        <div role="group" aria-label="Hours a week" className={`pp-hours-group${narrow ? ' is-narrow' : ''}`}>
          {!narrow && <span className="kicker">Hours a week</span>}
          {hourChoices(state).map((v) => (
            <button key={v} type="button" className={`numeral pp-seg${v === h ? ' is-on' : ''}`} aria-pressed={v === h} onClick={() => onHours(v)}>
              {narrow ? `${v} h a week` : `${v} h`}
            </button>
          ))}
        </div>
      </div>
      {!narrow && <p className="numeral pp-meta pp-plan-summary">{summary}</p>}

      {narrow ? (
        <div className="pp-phase-list">
          {move.phases.map((p, i) => {
            const on = i === cur;
            const prog = phaseProgress(move, i, state.done);
            const panelId = `pp-phase-${i}`;
            return (
              <div key={i} className={`pp-phase-item${on ? ' is-on' : ''}`}>
                <button type="button" className="pp-phase-row" aria-expanded={on} aria-controls={panelId} onClick={() => onPhase(i)}>
                  <span className={`numeral pp-phase-n${prog.all > 0 && prog.done === prog.all ? ' is-done' : ''}`} aria-hidden="true">
                    {i + 1}
                  </span>
                  <span className="pp-phase-row-title">{p.title}</span>
                  <span className="numeral pp-meta">{spans[i].label}</span>
                </button>
                <div id={panelId} hidden={!on}>
                  {on && <PhaseDetail move={move} phase={p} index={i} weeks={spans[i].label} state={state} onTask={onTask} onLink={onLink} narrow />}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <>
          <div className="pp-pills" role="group" aria-label="Phases">
            {move.phases.map((p, i) => {
              const on = i === cur;
              const prog = phaseProgress(move, i, state.done);
              const frac = prog.all ? prog.done / prog.all : 0;
              return (
                <button key={i} type="button" className={`pp-pill${on ? ' is-on' : ''}`} aria-pressed={on} onClick={() => onPhase(i)}>
                  <span className="numeral pp-meta">{spans[i].label}</span>
                  <span className="pp-pill-title">{p.title}</span>
                  <span className="pp-track" aria-hidden="true">
                    <span className="pp-track-bar" style={{ transform: `scaleX(${frac})` }} />
                  </span>
                  <span className="sr-only">
                    {prog.done} of {prog.all} done
                  </span>
                </button>
              );
            })}
          </div>
          {move.phases[cur] && (
            <div className="pp-phase">
              <PhaseDetail
                move={move}
                phase={move.phases[cur]}
                index={cur}
                weeks={spans[cur].label}
                state={state}
                onTask={onTask}
                onLink={onLink}
                narrow={false}
              />
            </div>
          )}
        </>
      )}
    </section>
  );
}

interface DetailProps {
  move: PrepareMove;
  phase: PreparePhase;
  index: number;
  weeks: string;
  state: PrepareState;
  narrow: boolean;
  onTask: (key: string, done: boolean) => void;
  onLink: (phaseIndex: number, url: string) => void;
}

/** One phase: its tasks as checkboxes, what the person will have, and the links they added. */
function PhaseDetail({ move, phase, index, weeks, state, narrow, onTask, onLink }: DetailProps) {
  const done = new Set(state.done);
  const links = state.links.filter((l) => l.moveId === move.id && l.phaseIndex === index);
  return (
    <div className="pp-detail">
      {!narrow && (
        <div className="pp-head">
          <h3 className="pp-h3">{phase.title}</h3>
          <span className="numeral pp-meta">
            {weeks} · {phase.hours} h
          </span>
        </div>
      )}
      <div className="pp-tasks">
        {phase.tasks.map((t, j) => {
          const key = taskKey(move.id, index, j);
          const id = `pp-task-${move.id}-${index}-${j}`;
          const on = done.has(key);
          return (
            <div key={key} className="pp-task">
              <input id={id} type="checkbox" className="pp-check" checked={on} onChange={(e) => onTask(key, e.target.checked)} />
              <label htmlFor={id} className={`pp-task-label${on ? ' is-done' : ''}`}>
                {t}
              </label>
            </div>
          );
        })}
      </div>
      <p className="pp-deliver">
        <span className="kicker">You'll have</span>
        <span>{phase.deliverable}</span>
      </p>
      <Links links={links} phaseIndex={index} onLink={onLink} />
    </div>
  );
}

function Links({ links, phaseIndex, onLink }: { links: PrepareLink[]; phaseIndex: number; onLink: (phaseIndex: number, url: string) => void }) {
  const id = `${useId()}-link`;
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState('');
  const [bad, setBad] = useState(false);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const url = webUrl(draft);
    if (!url) {
      setBad(true);
      return;
    }
    onLink(phaseIndex, url);
    setDraft('');
    setBad(false);
    setAdding(false);
  };

  return (
    <div className="pp-links">
      {links.length > 0 && (
        <ul className="pp-link-list" aria-label="What you shipped">
          {links.map((l) => (
            <li key={`${l.url}-${l.addedAt}`}>
              <a href={l.url} target="_blank" rel="noopener noreferrer">
                {hostPath(l.url)}
              </a>
              <span className="kicker"> · added {shortDate(new Date(l.addedAt))}</span>
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <form className="pp-linkform" onSubmit={submit}>
          <label htmlFor={id} className="sr-only">
            Link to what you shipped
          </label>
          <input
            id={id}
            type="url"
            className="field pp-linkfield"
            placeholder="https://…"
            value={draft}
            autoFocus
            aria-invalid={bad || undefined}
            aria-describedby={bad ? `${id}-err` : undefined}
            onChange={(e) => {
              setDraft(e.target.value);
              setBad(false);
            }}
          />
          <button type="submit" className="btn btn-sm pp-btn-sm">
            Keep it
          </button>
          <button type="button" className="btn btn-sm btn-quiet pp-btn-sm" onClick={() => setAdding(false)}>
            Cancel
          </button>
          {bad && (
            <span id={`${id}-err`} className="pp-err-line" role="alert">
              That needs to be a web link (https://…).
            </span>
          )}
        </form>
      ) : (
        <p className="pp-muted pp-link-note">
          Finished this phase? Add the link to what you shipped; it is kept with the plan.{' '}
          <button type="button" className="btn btn-quiet pp-link" onClick={() => setAdding(true)}>
            Add a link
          </button>
        </p>
      )}
    </div>
  );
}
