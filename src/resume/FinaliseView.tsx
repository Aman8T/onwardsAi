/**
 * Finalise: the one place a resume is checked before it can leave. A draft is written freely; here every line the
 * person typed is checked in code against the facts it cites.
 *
 * What only the person can know (a figure, a name, a term, a claim about their own part that no source has) is a
 * finding with two answers: it is true and stays as their own words, or the edit is undone. The answers are
 * collected and sent together. With nothing left, the files are exported and read back, and that read-back is shown
 * as a checklist: each item is ok or open, never a score.
 */
import { useEffect, useMemo, useState } from 'react';
import { countOf, factIndex, findingNote, sheetLines, shortDate, sourceOf, splitFinding } from './model';
import { FileLinks, ScreenLink, Stamp } from './parts';
import type { ResumeQuery } from './route';
import type { Busy, FileKind, FinaliseReport, Finding, FindingAnswer, FindingChoice, LintReport, ResumeDetail } from './types';

export interface FinaliseViewProps {
  detail: ResumeDetail;
  status: 'idle' | 'running' | 'ready' | 'error';
  report?: FinaliseReport;
  /** Why the last run failed. */
  error?: string;
  /** The person came here through "Make default". */
  wantsDefault: boolean;
  busy: Busy | null;
  notice?: string;
  /** Run the checks, with the answers collected so far. */
  onRun: (req: { makeDefault: boolean; answers: FindingAnswer[] }) => void;
  onMakeDefault: () => void;
  onDownload: (file: FileKind) => void;
  hrefFor: (file: FileKind) => string;
  onNavigate: (to: ResumeQuery) => void;
}

const CHOICE_LABEL: Record<FindingChoice, string> = {
  mine: 'It is true. Keep it as my words',
  undo: 'Undo my edit',
};
const CHOICE_ORDER: readonly FindingChoice[] = ['mine', 'undo'];

export function FinaliseView(props: FinaliseViewProps) {
  const { detail, status, report, error, wantsDefault, busy, notice, onRun, onMakeDefault, onDownload, hrefFor, onNavigate } = props;
  const { resume } = detail;
  const findings = report && !report.done ? report.findings : [];
  const [answers, setAnswers] = useState<ReadonlyMap<string, FindingChoice>>(new Map());

  // a new report asks its own questions: answers to the one before are not carried over
  useEffect(() => {
    setAnswers(new Map());
  }, [report]);

  const lines = useMemo(() => sheetLines(detail.variant, detail.marks), [detail.variant, detail.marks]);
  const byId = useMemo(() => factIndex(detail.facts), [detail.facts]);

  const running = status === 'running';
  const done = report?.done === true;
  // finalised earlier and untouched since: there is nothing to run
  const already = !report && resume.state === 'final';
  const left = findings.filter((f) => !answers.has(f.bulletId)).length;
  const ready = !running && busy === null && left === 0;
  const lint: LintReport | undefined = report?.lint ?? (already ? detail.variant.lint : undefined);
  const back: ResumeQuery = { screen: 'resume', id: resume.id };

  const run = (makeDefault: boolean) => {
    if (!ready) return;
    onRun({ makeDefault, answers: findings.flatMap((f) => (answers.has(f.bulletId) ? [{ bulletId: f.bulletId, choice: answers.get(f.bulletId) as FindingChoice }] : [])) });
  };
  const answer = (bulletId: string, choice: FindingChoice) => setAnswers((prev) => new Map(prev).set(bulletId, choice));

  return (
    <>
      <header className="rs-intro">
        <p className="kicker">
          Finalise · {resume.title}
          {resume.changes > 0 ? ` · ${countOf(resume.changes, 'change')} from ${resume.kind === 'upload' ? 'the file as you wrote it' : 'your uploaded resume'}` : ''}
        </p>
        <h1 className="serif rs-title" data-rs-h1 tabIndex={-1}>
          You wrote freely. Now it is checked, once, before it can leave.
        </h1>
        <p className="rs-lede">Two things can happen to what the checks find. Leafout fixes what is only layout. It asks you about anything only you can know.</p>
      </header>

      <p className="rs-notice" role="alert">
        {status === 'error' ? error : notice}
      </p>

      <div className="rs-two">
        {/* what the checks found, by who settles it */}
        <div className="rs-stack">
          <section className="rs-group" aria-labelledby="rs-fz-you">
            <h2 id="rs-fz-you" className="kicker">
              Only you can answer
              {findings.length > 0 && (
                <>
                  {' · '}
                  <span className="numeral">{left}</span> left
                </>
              )}
            </h2>
            {findings.length > 0 ? (
              findings.map((f) => {
                const line = lines.find((l) => l.bullet?.id === f.bulletId);
                return (
                  <FindingCard
                    key={f.bulletId}
                    finding={f}
                    where={[line ? `block ${line.no}` : '', line?.entryTitle ?? '', 'edited by you in this draft'].filter(Boolean).join(' · ')}
                    note={findingNote(f, sourceOf(line?.bullet ? byId.get(line.bullet.factIds[0] ?? '') : undefined))}
                    choice={answers.get(f.bulletId)}
                    disabled={running}
                    onChoose={(choice) => answer(f.bulletId, choice)}
                  />
                );
              })
            ) : (
              <p className="rs-small rs-muted">
                {running
                  ? 'Checking every line you typed against the facts it cites…'
                  : done || already
                    ? 'Nothing is waiting on you. Every line rests on a fact, or on your own word for it.'
                    : 'The checks have not run yet. Anything they cannot settle from your blocks shows here, with two answers.'}
              </p>
            )}
            {findings.length > 0 && (
              <p className="rs-fine">Kept as your words, a line becomes a block of its own, labelled as yours wherever it shows. Undone, the line goes back to the words of its source.</p>
            )}
          </section>

          {report && report.fixed.length > 0 && (
            <section className="rs-group" aria-labelledby="rs-fz-fixed">
              <h2 id="rs-fz-fixed" className="kicker">
                Fixed for you · <span className="numeral">{report.fixed.length}</span>
              </h2>
              <ul className="panel rs-rows rs-rows-in">
                {report.fixed.map((text, i) => (
                  <li key={i} className="rs-row">
                    <span className="rs-small">{text}</span>
                    <Stamp>done</Stamp>
                  </li>
                ))}
              </ul>
              <p className="rs-fine">These change the file's layout, never your words. Your blocks are untouched.</p>
            </section>
          )}
        </div>

        {/* the two buttons, what they unlock, and the read-back */}
        <div className="rs-stack">
          <section className="panel rs-card" aria-labelledby="rs-fz-go">
            {done || already ? (
              <>
                <div className="rs-card-head">
                  <h2 id="rs-fz-go" className="kicker">
                    Finalised{resume.finalisedAt ? ` · ${shortDate(resume.finalisedAt)}` : ''}
                  </h2>
                  <Stamp>done</Stamp>
                </div>
                <div className="rs-actions">
                  <FileLinks hrefFor={hrefFor} onDownload={onDownload} busy={busy} />
                  {!resume.isDefault && (
                    <button type="button" className="btn btn-sm rs-btn" disabled={busy !== null} aria-busy={busy?.kind === 'default'} onClick={onMakeDefault}>
                      {busy?.kind === 'default' ? 'Making it your default…' : 'Make default'}
                    </button>
                  )}
                </div>
                <p className="rs-fine" role="status">
                  {resume.isDefault ? 'This is your default resume.' : 'This version is locked and can be downloaded.'}
                </p>
                <ul className="rs-list rs-small">
                  <li>Editing it again starts a new draft. This finalised version stays until the next one replaces it.</li>
                  <li>
                    <ScreenLink to={back} onNavigate={onNavigate}>
                      Back to the resume
                    </ScreenLink>
                  </li>
                </ul>
              </>
            ) : (
              <>
                <h2 id="rs-fz-go" className="kicker">
                  {findings.length > 0 ? `When ${findings.length === 1 ? 'the one answer is' : `the ${findings.length} answers are`} in` : 'Before it can leave'}
                </h2>
                <div className="rs-actions">
                  <button type="button" className={`btn rs-btn${wantsDefault ? '' : ' btn-primary'}`} aria-disabled={!ready} aria-describedby="rs-fz-hint" onClick={() => run(false)}>
                    {running && !wantsDefault ? 'Checking…' : 'Finalise'}
                  </button>
                  {!resume.isDefault && (
                    <button type="button" className={`btn rs-btn${wantsDefault ? ' btn-primary' : ''}`} aria-disabled={!ready} aria-describedby="rs-fz-hint" onClick={() => run(true)}>
                      {running && wantsDefault ? 'Checking…' : 'Finalise and make default'}
                    </button>
                  )}
                </div>
                <p id="rs-fz-hint" className="rs-fine" role="status">
                  {running
                    ? 'Running the checks.'
                    : left > 0
                      ? `${countOf(left, 'answer')} left. ${resume.isDefault ? 'The button wakes' : 'Both buttons wake'} when ${left === 1 ? 'it is' : 'they are'} settled.`
                      : findings.length > 0
                        ? 'Every answer is in. Finalise sends them and runs the checks again.'
                        : 'The checks run when you press Finalise.'}
                </p>
                <ul className="rs-list rs-small">
                  <li>
                    <span className="rs-strong">Finalise</span> locks this version. It can then be downloaded as PDF or Word.
                  </li>
                  {!resume.isDefault && (
                    <li>
                      <span className="rs-strong">Make default</span> does the same, then makes it your default resume.
                    </li>
                  )}
                  <li>A draft is not downloaded. Only a finalised resume is.</li>
                  <li>Editing it again starts a new draft. The finalised version stays until the next one replaces it.</li>
                </ul>
              </>
            )}
          </section>

          <section className="panel rs-card" aria-labelledby="rs-fz-ats">
            <div>
              <h2 id="rs-fz-ats" className="kicker">
                Read back the way an applicant system reads it
              </h2>
              <p className="rs-fine">Leafout exports the file, then extracts its text again and checks what came out. A checklist, never a score.</p>
            </div>
            {lint && lint.items.length > 0 ? (
              <ul className="rs-checks">
                {lint.items.map((item) => (
                  <li key={item.id} className="rs-check" data-ok={item.ok}>
                    <span className={`rs-check-state${item.ok ? '' : ' is-open'}`}>{item.ok ? 'ok' : 'open'}</span>
                    <span className="rs-row-main">
                      <span className="rs-small">{item.label}</span>
                      {(item.detail || item.location) && <span className="rs-fine">{[item.detail, item.location].filter(Boolean).join(' · ')}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="rs-small rs-muted">
                {done || already ? 'The read-back did not come with this answer.' : 'The read-back runs on the exported files, once nothing is waiting on you.'}
              </p>
            )}
          </section>
        </div>
      </div>
    </>
  );
}

function FindingCard({
  finding,
  where,
  note,
  choice,
  disabled,
  onChoose,
}: {
  finding: Finding;
  where: string;
  note: string;
  choice: FindingChoice | undefined;
  disabled: boolean;
  onChoose: (choice: FindingChoice) => void;
}) {
  const { before, what, after } = splitFinding(finding);
  const options = CHOICE_ORDER.filter((c) => finding.options.includes(c));
  return (
    <article className="panel rs-card rs-finding" data-finding={finding.bulletId} data-kind={finding.kind}>
      <span className="rs-cite">{where}</span>
      <p className="rs-finding-text">
        {before}
        {what && <mark className="rs-mark">{what}</mark>}
        {after}
      </p>
      <p className="rs-small rs-muted">{note}</p>
      <div className="rs-actions" role="group" aria-label="Your answer">
        {options.map((c) => (
          <button key={c} type="button" className={`btn btn-sm rs-btn rs-choice${c === 'undo' ? ' btn-quiet' : ''}`} aria-pressed={choice === c} disabled={disabled} onClick={() => onChoose(c)}>
            {CHOICE_LABEL[c]}
          </button>
        ))}
      </div>
    </article>
  );
}
