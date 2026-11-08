/**
 * `/apply/<id>`: one application, shown by its stage. This is the shell's stub; package FE2 replaces it with the run
 * screens (Filling, Questions, Review, Needs you and the outcomes) and keeps the same props.
 *
 * For now it reads the application, polls every 3 s while the worker is still moving it, and shows where it stands.
 * It offers no approve, retry or resend of any kind.
 */
import { useCallback, useEffect, useState } from 'react';
import type { ApplicationDetail, ApplyStatus } from '../../shared/apply';
import { ATS_LABEL, STAGE_LABEL, STOP_REASON_TEXT, applyApi, busy, errorText, stageTone } from './api';
import { LIST_ROUTE, linkTo } from './route';
import type { Navigate } from './route';

export interface ApplicationScreenProps {
  id: string;
  navigate: Navigate;
  status: ApplyStatus;
}

const POLL_MS = 3000;

export function ApplicationScreen({ id, navigate }: ApplicationScreenProps) {
  const [detail, setDetail] = useState<ApplicationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDetail(await applyApi.get(id));
      setError(null);
    } catch (err) {
      setError(errorText(err, 'Could not load this application. Try again.'));
    }
  }, [id]);

  useEffect(() => {
    setDetail(null);
    void load();
  }, [load]);

  const moving = detail ? busy(detail.stage) : false;
  useEffect(() => {
    if (!moving) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [moving, load]);

  if (!detail) {
    return error ? (
      <p className="ap-error" role="alert">
        {error}
      </p>
    ) : (
      <p className="ap-muted" role="status">
        Loading the application
      </p>
    );
  }

  return <ApplicationSummaryView detail={detail} navigate={navigate} />;
}

/** The stub's one view: the role, its stage and why. */
export function ApplicationSummaryView({ detail, navigate }: { detail: ApplicationDetail; navigate: Navigate }) {
  const why = detail.stageReason ?? (detail.stopReason ? STOP_REASON_TEXT[detail.stopReason] : undefined);
  return (
    <div className="ap-stack ap-narrow">
      <div className="ap-head">
        <p className="kicker">
          <a {...linkTo(LIST_ROUTE, navigate)}>Applications</a> · {[detail.company ?? detail.domain, ATS_LABEL[detail.ats]].filter(Boolean).join(' · ')}
        </p>
        <h1 className="serif ap-title">{detail.title ?? detail.domain}</h1>
      </div>
      <section className="panel ap-panel ap-stack-s" aria-live="polite">
        <span className={`chip ap-chip ap-tone-${stageTone(detail.stage, detail.needsCount)} ap-self-start`}>{STAGE_LABEL[detail.stage]}</span>
        {why && <p>{why}</p>}
        {busy(detail.stage) && <p className="ap-muted ap-small">This page updates on its own. Nothing is sent before you approve.</p>}
        {detail.manualUrl && (
          <div className="ap-actions">
            <a className="btn" href={detail.manualUrl} target="_blank" rel="noreferrer noopener">
              Open the form on the employer's site
            </a>
          </div>
        )}
      </section>
    </div>
  );
}
