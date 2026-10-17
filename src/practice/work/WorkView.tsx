/**
 * The work sample's workspace, drawn (board `WorkSample` of the Interview canvas): the task and its material, the
 * person's own work and how they'd test it, and the optional assistant. "I'm done, ask me about it" hands the work in;
 * the interviewer then asks about the choices, in the room.
 *
 * What is not here, on purpose: the checks the task looks for. They stay on the server until the debrief, so this page
 * cannot show them, and shows how they will be shown instead. Nothing runs here; the interviewer reads what is written.
 */
import { useId } from 'react';
import type { WorkTask } from '../../../shared/interview';
import { plural } from '../../lib/ui';
import '../../styles/practice-room.css';
import { MicIcon } from '../room/icons';
import { MicMeter } from '../room/MicMeter';
import type { RoomActions, RoomModel } from '../room/model';
import { FocusBar } from '../room/RoomView';
import { ListenClock } from '../room/parts';
import { MIC_BLOCKED, NO_EMPLOYER, NO_SPEECH, SPEECH_NETWORK, WRITING_WORDS, engineNote, privacyLine, questionsWord } from '../words';
import { AssistantPanel } from './AssistantPanel';
import type { AssistantHandlers, AssistantState } from './AssistantPanel';
import { Material } from './Material';
import type { Dictation } from './useDictation';

export interface WorkViewProps {
  model: RoomModel;
  actions: RoomActions;
  task: WorkTask;
  work: string;
  testNote: string;
  minutesIn: number;
  handing: boolean;
  handError?: string;
  assistant: AssistantState;
  talk: Dictation;
  /** Dev gallery: fixed meter bars instead of the microphone. */
  meterPreview?: readonly number[];
  onWork(value: string): void;
  onTestNote(value: string): void;
  onHandIn(): void;
  onTalk(on: boolean): void;
  assistantHandlers: AssistantHandlers;
}

const STEP = (label: string, state: 'now' | 'next') => (
  <li key={label} aria-current={state === 'now' ? 'step' : undefined} className={`rm-step${state === 'now' ? ' is-now' : ''}`}>
    <span className="rm-step-sq" aria-hidden="true" />
    <span>{label}</span>
  </li>
);

export function WorkView(p: WorkViewProps) {
  const ids = useId();
  const { task, model, talk } = p;
  const isCode = task.materialKind === 'code';
  const questions = model.total;
  const canHand = !p.handing && p.work.trim() !== '';
  const noteLabel = isCode ? "How you'd test it" : "How you'd check your work";
  const talkIssue = talk.issue === 'blocked' ? MIC_BLOCKED : talk.issue === 'network' ? SPEECH_NETWORK : null;

  return (
    <div className="rm-room rm-workroom">
      <FocusBar model={model} actions={p.actions} />

      <main className="rm-main">
        <div className="rm-work-top">
          <nav aria-label="Where you are">
            <ol className="rm-steps">
              {STEP('The task', 'now')}
              {STEP(`${questionsWord(questions)} on your choices`, 'next')}
              {STEP('What the task looked for', 'next')}
            </ol>
          </nav>
          <span className="rm-faint rm-small">The checks stay hidden until afterwards. No verdicts until the end.</span>
        </div>

        <div className="rm-work-cols">
          <section className="panel rm-task" aria-labelledby={`${ids}-task`}>
            <div className="rm-task-head">
              <div id={`${ids}-task`} className="kicker">
                The task
              </div>
              <h1 className="rm-q rm-q-task">{task.task}</h1>
            </div>

            <div className="rm-task-block">
              <div className="kicker">Material</div>
              <Material task={task} caption={isCode ? `This is the version to work from. Write your fix in your own version, in the middle.` : undefined} />
            </div>

            <div className="rm-task-block">
              <div className="kicker">What happens next</div>
              <p className="rm-muted">When you&apos;re done, the interviewer asks about your choices.</p>
            </div>

            <div className="rm-task-block rm-task-foot">
              <p className="rm-strong">About {plural(task.minutes, 'minute')} · a guide, not a limit</p>
              <p className="rm-muted rm-small">{p.minutesIn} min in</p>
              <p className="rm-faint rm-small">There is no countdown. Take the time you need.</p>
            </div>
          </section>

          <section className="panel rm-yours" aria-labelledby={`${ids}-work-h`}>
            <div className="rm-yours-head">
              <div>
                <div id={`${ids}-work-h`} className="kicker">
                  Your work
                </div>
                <label htmlFor={`${ids}-work`} className="rm-strong">
                  {isCode ? `Your version of ${task.materialName}` : 'Your answer'}
                </label>
              </div>
              <span className="rm-faint rm-small">Nothing runs here. The interviewer reads what you write.</span>
            </div>

            <textarea
              id={`${ids}-work`}
              className={`rm-textarea${isCode ? ' rm-codearea' : ''}`}
              rows={isCode ? 22 : 12}
              wrap={isCode ? 'off' : 'soft'}
              spellCheck={!isCode}
              maxLength={20000}
              value={p.work}
              placeholder={isCode ? undefined : 'Write your answer here.'}
              disabled={p.handing}
              onChange={(e) => p.onWork(e.target.value)}
            />

            <div className="rm-write">
              <label htmlFor={`${ids}-note`} className="kicker">
                {noteLabel}
              </label>
              <textarea
                id={`${ids}-note`}
                className="rm-textarea"
                rows={3}
                maxLength={2000}
                value={p.testNote}
                disabled={p.handing}
                onChange={(e) => p.onTestNote(e.target.value)}
              />
            </div>

            {(talk.on || talkIssue || talk.issue === 'unsupported') && (
              <div className="rm-talk">
                <div className="kicker">Talking it through</div>
                {talk.issue === 'unsupported' ? (
                  <p className="rm-muted">{NO_SPEECH}</p>
                ) : talkIssue ? (
                  <p role="alert">
                    <b>{talkIssue.title}</b> {talkIssue.body}
                  </p>
                ) : (
                  <>
                    <div className="rm-listenrow">
                      <MicMeter active={talk.on && !p.meterPreview} preview={p.meterPreview} onBlocked={(why) => why === 'denied' && talk.blocked()} />
                      <span className="rm-listenlabel">
                        <span aria-live="polite">Listening</span> · <ListenClock read={talk.clock} />
                      </span>
                    </div>
                    <p className="rm-said" aria-live="polite">
                      {talk.interim ? (
                        <span className="rm-interim">{talk.interim}</span>
                      ) : talk.writing ? (
                        <span className="rm-interim">{WRITING_WORDS}</span>
                      ) : (
                        <span className="rm-faint">
                          {talk.by === 'sarvam' ? 'Your words are added to the box above after each pause.' : 'Your words are added to the box above as you speak.'} Fix anything before you hand in.
                        </span>
                      )}
                    </p>
                  </>
                )}
                {talk.note && (
                  <p className="rm-muted rm-small" role="status">
                    {talk.note}
                  </p>
                )}
                <p className="rm-faint rm-small">
                  {privacyLine(talk.by)} {engineNote(talk.by)}
                </p>
                <div>
                  <button type="button" className="btn btn-quiet btn-sm rm-btn-sm" onClick={() => p.onTalk(false)}>
                    Back to typing
                  </button>
                </div>
              </div>
            )}

            {p.handError && (
              <p className="rm-danger" role="alert">
                {p.handError}
              </p>
            )}

            <div className="rm-buttons">
              <button type="button" className="btn btn-primary rm-btn" disabled={!canHand} onClick={p.onHandIn}>
                {p.handing ? 'Handing in…' : "I'm done, ask me about it"}
              </button>
              {!talk.on && (
                <button type="button" className="btn rm-btn" onClick={() => p.onTalk(true)} disabled={p.handing}>
                  <MicIcon />
                  Talk it through instead
                </button>
              )}
            </div>
            <div role="status" aria-live="polite">
              {p.handing && <p className="rm-muted rm-small">Your work is saved for the questions. The interviewer will ask about your choices next.</p>}
            </div>
          </section>

          <AssistantPanel state={p.assistant} handlers={p.assistantHandlers} />
        </div>

        <div className="rm-work-after">
          <section className="panel rm-after" aria-labelledby={`${ids}-defend`}>
            <div className="kicker">After the task</div>
            <h2 id={`${ids}-defend`} className="rm-h2">
              Then: defend it
            </h2>
            <p className="rm-muted">
              {questionsWord(questions)} on your choices. The interviewer asks about what you actually wrote.
            </p>
            <p className="rm-muted rm-small">Afterwards you&apos;ll see the checks this task was looking for, each with what you did.</p>
          </section>
        </div>

        <p className="rm-faint rm-small rm-work-foot">
          {privacyLine(talk.by)} {NO_EMPLOYER}
        </p>
      </main>
    </div>
  );
}
