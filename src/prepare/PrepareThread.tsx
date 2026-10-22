/**
 * The Prepare answer in a thread: "How do I prepare for this role?" as four typed blocks (Where you stand, The one
 * move, Your plan, Next) instead of prose, with "Read this as text" written from the same blocks. The run itself
 * lives in `runs.ts`, so it carries on when the person switches threads; this component owns only what is open on
 * screen (a gap, the plan, a phase, an action).
 *
 * How it arrives (the Stream board): the gaps draw at once from the graph; the move and the plan show quiet outlines
 * until the model's calls land; "Stop here" aborts and keeps what arrived; an error shows a calm message with "Try
 * again"; "Make a new plan" asks for a fresh one.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { PrepareRequest } from '../../shared/prepare';
import { IconRefresh, IconStop } from '../components/Icons';
import type { PrepareClient } from './api';
import { JumpBar } from './blocks/JumpBar';
import { MoveBlock } from './blocks/Move';
import { NextBlock } from './blocks/NextBlock';
import { PlanBlock } from './blocks/PlanBlock';
import { ReadAsText } from './blocks/ReadAsText';
import { MoveSkeleton, PlanSkeleton, StandingSkeleton } from './blocks/Skeletons';
import { Standing } from './blocks/Standing';
import { evidenceIndex, planText } from './plan';
import { bindRun, getRun, retrySave, runKey, startRun, stopRun, tickPractice, updateState, usePrepareRun } from './runs';
import '../styles/prepare.css';

export interface PrepareThreadProps {
  client: PrepareClient;
  profileId: string;
  roleId: string;
  /** One gap of the role: a plan for just that. */
  requirement?: string;
  /** The stored plan's id, once known: the thread opens it directly. */
  planId?: string;
  onPlanId?: (planId: string) => void;
  /** "Help me with just this" under a gap: opens a plan for that one gap. */
  onJustThis?: (requirement: string) => void;
  /** The Practice action. */
  onPractice: () => void;
  /** Re-drill what wobbled, from a ticked Fix step (I2); absent: no link. */
  onRedrill?: () => void;
  /** Phone width: the jump bar, the phone's plan list and sticky move actions. */
  narrow: boolean;
}

export function PrepareThread({ client, profileId, roleId, requirement, planId, onPlanId, onJustThis, onPractice, onRedrill, narrow }: PrepareThreadProps) {
  const key = runKey(profileId, roleId, requirement);
  const run = usePrepareRun(key);

  const onPlanIdRef = useRef(onPlanId);
  onPlanIdRef.current = onPlanId;

  useEffect(() => {
    const req: PrepareRequest = {
      profileId,
      roleId,
      ...(requirement ? { requirement } : {}),
    };
    bindRun(key, { client, req, onPlanId: (id) => onPlanIdRef.current?.(id) });
    if (getRun(key).phase === 'idle') startRun(key, { planId });
  }, [key, client, profileId, roleId, requirement, planId]);

  /* what is open on screen */
  const [openGap, setOpenGap] = useState<string | null | undefined>(undefined);
  const [planOpen, setPlanOpen] = useState<boolean | null>(null);
  const [phase, setPhase] = useState<{ moveId: string; index: number } | null>(null);
  const [confirmFresh, setConfirmFresh] = useState(false);

  const plan = run.plan;
  const evidence = useMemo(() => (plan ? evidenceIndex(plan) : new Map()), [plan]);
  const moveCount = plan?.moves.length ?? 0;
  const moveIndex = plan && moveCount ? Math.min(Math.max(0, plan.state.moveIndex), moveCount - 1) : 0;
  const move = plan?.moves[moveIndex];
  const phaseIndex = move && phase && phase.moveId === move.id ? phase.index : 0;
  const biggest = plan?.gaps.find((g) => g.biggest)?.name ?? plan?.gaps[0]?.name ?? null;
  const gapOpen = openGap === undefined ? biggest : openGap;
  const showPlan = planOpen ?? true;

  const streaming = run.phase === 'streaming' || run.phase === 'loading';
  const ready = run.phase === 'ready';

  const paragraphs = useMemo(() => (plan ? planText(plan, move, plan.state.hoursPerWeek) : []), [plan, move]);

  const jump = [
    { id: 'pp-stand', label: 'Stand', present: !!plan },
    { id: 'pp-move', label: 'Move', present: !!move },
    { id: 'pp-plan', label: 'Plan', present: !!move && showPlan },
    { id: 'pp-next', label: 'Next', present: !!move },
  ];

  return (
    <div className={`pp-msg${narrow ? ' is-narrow' : ''}`}>
      <span className="kicker pp-who" aria-hidden="true">
        Leafout
      </span>
      <span className="sr-only">Leafout: </span>
      <div className={`pp-root${narrow ? ' is-narrow' : ''}`} aria-busy={streaming}>
        {narrow && plan && <JumpBar items={jump} />}

        {plan ? (
          <Standing
            plan={plan}
            evidence={evidence}
            waiting={streaming && !run.standing}
            openGap={gapOpen}
            onToggleGap={(name) => setOpenGap(gapOpen === name ? null : name)}
            onJustThis={plan.scope === 'role' ? onJustThis : undefined}
          />
        ) : (
          streaming && <StandingSkeleton />
        )}

        {move ? (
          <MoveBlock
            move={move}
            index={moveIndex}
            count={moveCount}
            evidence={evidence}
            planOpen={showPlan}
            onTogglePlan={() => setPlanOpen(!showPlan)}
            onNext={() => {
              updateState(key, (s) => ({
                ...s,
                moveIndex: (moveIndex + 1) % moveCount,
              }));
              setPhase(null);
            }}
          />
        ) : (
          plan && streaming && <MoveSkeleton />
        )}

        {move && showPlan && plan ? (
          <PlanBlock
            move={move}
            state={plan.state}
            narrow={narrow}
            phaseIndex={phaseIndex}
            onPhase={(i) => setPhase({ moveId: move.id, index: i })}
            {...(plan.practice ? { practice: plan.practice } : {})}
            createdAt={plan.createdAt}
            onPracticeTick={(task, done) => tickPractice(key, task.id, done)}
            {...(onRedrill ? { onRedrill } : {})}
            onHours={(h) => updateState(key, (s) => (s.hoursPerWeek === h ? s : { ...s, hoursPerWeek: h }))}
            onTask={(taskKey, done) =>
              updateState(key, (s) => {
                const has = s.done.includes(taskKey);
                if (has === done) return s;
                return {
                  ...s,
                  done: done ? [...s.done, taskKey] : s.done.filter((k) => k !== taskKey),
                };
              })
            }
            onLink={(phaseIdx, url) =>
              updateState(key, (s) => ({
                ...s,
                links: [
                  ...s.links,
                  {
                    moveId: move.id,
                    phaseIndex: phaseIdx,
                    url,
                    addedAt: new Date().toISOString(),
                  },
                ],
              }))
            }
          />
        ) : (
          plan && streaming && !move && <PlanSkeleton />
        )}

        {move && plan && <NextBlock plan={plan} move={move} client={client} evidence={evidence} onPractice={onPractice} narrow={narrow} />}

        {run.phase === 'streaming' && (
          <div className="pp-stream" role="status">
            <span className="kicker">
              {run.standing && run.moves ? 'Saving the plan…' : plan ? 'The model is filling in the rest…' : 'Reading the role on your graph…'}
            </span>
            <button type="button" className="btn btn-sm pp-btn-sm" onClick={() => stopRun(key)}>
              <IconStop size={14} strokeWidth={1.5} />
              <span>Stop here</span>
            </button>
          </div>
        )}

        {run.phase === 'stopped' && (
          <div className="pp-stream" role="status">
            <span className="kicker">
              {move ? 'Stopped here. The plan is not saved; ticks and links stay on this screen.' : 'Stopped before the plan arrived.'}
            </span>
            <button type="button" className="btn btn-sm pp-btn-sm" onClick={() => startRun(key)}>
              <IconRefresh size={14} strokeWidth={1.5} />
              <span>{move ? 'Carry on' : 'Try again'}</span>
            </button>
          </div>
        )}

        {run.phase === 'error' && (
          <div className="pp-error" role="alert">
            <span>
              <strong>{plan ? "The plan couldn't be finished." : 'This plan could not be made.'}</strong> {run.error}
            </span>
            <button type="button" className="btn btn-sm pp-btn-sm" onClick={() => startRun(key)}>
              <IconRefresh size={14} strokeWidth={1.5} />
              <span>Try again</span>
            </button>
          </div>
        )}

        {run.save === 'failed' && (
          <div className="pp-error" role="alert">
            <span>
              <strong>That change was not saved.</strong> {run.saveError} It stays on this screen.
            </span>
            <button type="button" className="btn btn-sm pp-btn-sm" onClick={() => retrySave(key)}>
              <IconRefresh size={14} strokeWidth={1.5} />
              <span>Save again</span>
            </button>
          </div>
        )}

        {ready && plan && (
          <>
            <ReadAsText paragraphs={paragraphs} />
            <div className="pp-foot">
              {confirmFresh ? (
                <>
                  <span className="kicker">A new plan replaces this one, its ticks and its links.</span>
                  <button
                    type="button"
                    className="btn btn-sm pp-btn-sm"
                    onClick={() => {
                      setConfirmFresh(false);
                      setPhase(null);
                      setOpenGap(undefined);
                      setPlanOpen(null);
                      startRun(key, { fresh: true });
                    }}
                  >
                    Make it
                  </button>
                  <button type="button" className="btn btn-sm btn-quiet pp-btn-sm" onClick={() => setConfirmFresh(false)}>
                    Keep this one
                  </button>
                </>
              ) : (
                <button type="button" className="btn btn-sm btn-quiet pp-btn-sm" onClick={() => setConfirmFresh(true)}>
                  Make a new plan
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
