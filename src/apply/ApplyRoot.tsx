/**
 * `/apply`: the auto-apply tracker. Paste a job link, keep your details once, watch each application move, review the filled
 * form (screenshot + every value and where it came from), answer what only you can, and approve. Nothing is sent before you
 * approve the exact values shown. `/apply?id=<id>` opens one application.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import type {
  ApplicantDetails,
  ApplicantProfile,
  ApplicationDetail,
  ApplicationStage,
  ApplicationSummary,
  ApplyMetrics,
  ApplyStatus,
} from '../../shared/apply';
import { applyApi, ApplyError, busy, STAGE_LABEL, TRACKING_STAGES } from './api';
import '../styles/apply.css';

const DETAIL_FIELDS: [keyof ApplicantDetails, string][] = [
  ['firstName', 'First name'],
  ['lastName', 'Last name'],
  ['email', 'Email'],
  ['phone', 'Phone'],
  ['location', 'Location'],
  ['currentCompany', 'Current company'],
  ['currentTitle', 'Current title'],
  ['linkedin', 'LinkedIn URL'],
  ['github', 'GitHub URL'],
  ['website', 'Website'],
  ['workAuthorization', 'Work authorization'],
  ['needsSponsorship', 'Need visa sponsorship? (Yes / No)'],
  ['willingToRelocate', 'Willing to relocate? (Yes / No)'],
  ['noticePeriod', 'Notice period'],
  ['salaryExpectation', 'Salary expectation'],
  ['startDate', 'Earliest start date'],
  ['howDidYouHear', 'How you heard about roles'],
  ['eeoGender', 'Gender (optional, only used if you fill it)'],
];

const SOURCE_LABEL: Record<string, string> = {
  details: 'your details',
  resume: 'your resume',
  answers: 'a saved answer',
  model: 'drafted from your facts',
  agent: 'AI agent',
  user: 'you',
  default: 'default',
};

function message(err: unknown): string {
  return err instanceof ApplyError ? err.message : 'Something went wrong. Try again.';
}

function idFromUrl(): string | null {
  const id = new URLSearchParams(window.location.search).get('id');
  return id && /^[A-Za-z0-9_-]{1,96}$/.test(id) ? id : null;
}

function StageChip({ stage, approvedAt }: { stage: ApplicationStage; approvedAt?: string }) {
  const label = stage === 'ready_to_submit' && approvedAt ? 'Submitting' : STAGE_LABEL[stage];
  return <span className={`chip ap-chip ap-chip-${stage}`}>{label}</span>;
}

export function ApplyRoot() {
  const [status, setStatus] = useState<ApplyStatus | null>(null);
  const [apps, setApps] = useState<ApplicationSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(idFromUrl());
  const [detail, setDetail] = useState<ApplicationDetail | null>(null);
  const [profile, setProfile] = useState<ApplicantProfile | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setApps((await applyApi.list()).applications);
      if (selected) setDetail(await applyApi.get(selected));
    } catch (err) {
      setError(message(err));
    }
  }, [selected]);

  useEffect(() => {
    applyApi
      .status()
      .then(async (s) => {
        setStatus(s);
        if (!s.enabled) return;
        setProfile(await applyApi.profile());
        await refresh();
      })
      .catch((err) => setError(message(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (status?.enabled) void refresh();
  }, [selected, status?.enabled, refresh]);

  const moving = useMemo(() => apps.some((a) => busy(a.stage, a.approvedAt)), [apps]);
  useEffect(() => {
    if (!moving) return;
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(timer);
  }, [moving, refresh]);

  const open = (id: string | null) => {
    setSelected(id);
    setDetail(null);
    const url = id ? `/apply?id=${encodeURIComponent(id)}` : '/apply';
    window.history.replaceState(null, '', url);
  };

  if (status && !status.enabled) {
    return (
      <main className="ap-page">
        <p className="kicker">Auto-apply</p>
        <h1 className="serif ap-title">Auto-apply is off on this server</h1>
        <p className="ap-muted">It is switched on with AUTO_APPLY_ENABLED. Everything else in Leafout works as before.</p>
        <a className="btn btn-quiet" href="/">Back to Leafout</a>
      </main>
    );
  }

  return (
    <main className="ap-page">
      <header className="ap-head">
        <p className="kicker">Auto-apply</p>
        <h1 className="serif ap-title">Apply with what you can prove</h1>
        <p className="ap-muted">
          Paste a job link. Leafout fills the form from your details, shows you exactly what will be sent, and submits only when
          you approve it. LinkedIn and other sites that forbid automation stay manual: we keep track of them for you.
        </p>
        {status && !status.signedIn && status.anonymous && <p className="ap-muted">Kept in this browser until you sign in.</p>}
      </header>
      {error && (
        <p className="ap-error" role="alert">
          {error}
        </p>
      )}
      <PasteLink
        onCreated={(d) => {
          void refresh();
          open(d.id);
        }}
      />
      {profile && <DetailsPanel profile={profile} onSaved={setProfile} />}
      <section className="ap-grid" aria-label="Your applications">
        <ol className="ap-list">
          {apps.length === 0 && <li className="ap-muted">No applications yet.</li>}
          {apps.map((a) => (
            <li key={a.id}>
              <button type="button" className={`ap-row${a.id === selected ? ' ap-row-on' : ''}`} onClick={() => open(a.id)}>
                <span className="ap-row-title">{a.title ?? a.domain}</span>
                <span className="ap-muted">{[a.company, a.location].filter(Boolean).join(' · ') || a.domain}</span>
                <StageChip stage={a.stage} approvedAt={a.approvedAt} />
                {a.needsCount > 0 && <span className="ap-muted">{a.needsCount} to answer</span>}
              </button>
            </li>
          ))}
        </ol>
        {detail && <DetailView detail={detail} onChange={(d) => { setDetail(d); void refresh(); }} onClose={() => open(null)} />}
      </section>
      <EmailPanel onChanged={() => void refresh()} />
      <MetricsPanel />
    </main>
  );
}

function PasteLink({ onCreated }: { onCreated: (d: ApplicationDetail) => void }) {
  const [url, setUrl] = useState('');
  const [busyNow, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      onCreated(await applyApi.create(url.trim()));
      setUrl('');
    } catch (error) {
      setErr(message(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="panel ap-paste" onSubmit={submit}>
      <label className="field ap-field-wide">
        <span>Paste a job link (Greenhouse, Lever, Ashby or any careers page)</span>
        <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://jobs.lever.co/…" required />
      </label>
      <button type="submit" className="btn btn-primary" disabled={busyNow}>
        {busyNow ? 'Reading the page…' : 'Start'}
      </button>
      {err && <p className="ap-error">{err}</p>}
    </form>
  );
}

function DetailsPanel({ profile, onSaved }: { profile: ApplicantProfile; onSaved: (p: ApplicantProfile) => void }) {
  const [draft, setDraft] = useState<ApplicantDetails>(profile.details);
  const [state, setState] = useState<string | null>(null);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    try {
      onSaved(await applyApi.saveDetails(draft));
      setState('Saved.');
    } catch (err) {
      setState(message(err));
    }
  };
  const upload = async (file: File | undefined) => {
    if (!file) return;
    try {
      onSaved(await applyApi.uploadResume(file));
      setState('Resume saved.');
    } catch (err) {
      setState(message(err));
    }
  };
  return (
    <details className="panel ap-details" open={!profile.details.email}>
      <summary className="ap-summary">
        Your details for every form{profile.hasResume ? ` · ${profile.resumeName ?? 'resume'} attached` : ' · no resume yet'}
      </summary>
      <form onSubmit={save} className="ap-form">
        {DETAIL_FIELDS.map(([key, label]) => (
          <label key={key} className="field">
            <span>{label}</span>
            <input value={draft[key] ?? ''} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />
          </label>
        ))}
        <label className="field">
          <span>Resume (PDF or DOCX)</span>
          <input type="file" accept=".pdf,.docx" onChange={(e) => void upload(e.target.files?.[0])} />
        </label>
        <div className="ap-actions">
          <button type="submit" className="btn btn-primary">Save details</button>
          {state && <span className="ap-muted">{state}</span>}
        </div>
        <p className="ap-muted">We only ever use what you type here. Questions we cannot answer from it are asked, never guessed.</p>
      </form>
    </details>
  );
}

function DetailView({ detail, onChange, onClose }: { detail: ApplicationDetail; onChange: (d: ApplicationDetail) => void; onClose: () => void }) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [remember, setRemember] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const act = async (run: () => Promise<ApplicationDetail>) => {
    setErr(null);
    try {
      onChange(await run());
    } catch (error) {
      setErr(message(error));
    }
  };
  const review = detail.review;
  const canApprove = detail.stage === 'ready_to_submit' && !detail.approvedAt && review && detail.needsCount === 0;
  return (
    <article className="panel ap-detail" aria-label="Application">
      <div className="ap-detail-head">
        <div>
          <h2 className="serif ap-h2">{detail.title ?? detail.domain}</h2>
          <p className="ap-muted">
            {[detail.company, detail.location, detail.ats !== 'unknown' ? detail.ats : null].filter(Boolean).join(' · ')}
          </p>
        </div>
        <StageChip stage={detail.stage} approvedAt={detail.approvedAt} />
      </div>
      {detail.stageReason && <p>{detail.stageReason}</p>}
      {detail.manualUrl && (
        <p>
          <a className="btn btn-quiet" href={detail.manualUrl} target="_blank" rel="noreferrer noopener">
            Open the application on the site
          </a>
        </p>
      )}
      {detail.needs.length > 0 && (
        <form
          className="ap-needs"
          onSubmit={(e) => {
            e.preventDefault();
            void act(() =>
              applyApi.answer(detail.id, {
                answers: Object.entries(answers).map(([key, value]) => ({ key, value })),
                remember,
              }),
            );
          }}
        >
          <h3 className="kicker">Needs your input</h3>
          {detail.needs.map((n) => (
            <label key={n.key} className="field">
              <span>
                {n.label}
                {n.required ? ' (required)' : ''} · <span className="ap-muted">{n.reason}</span>
              </span>
              {n.options && n.options.length > 0 ? (
                <select value={answers[n.key] ?? ''} onChange={(e) => setAnswers({ ...answers, [n.key]: e.target.value })}>
                  <option value="">Choose…</option>
                  {n.options.map((o) => (
                    <option key={o} value={o}>
                      {o}
                    </option>
                  ))}
                </select>
              ) : n.kind === 'checkbox' ? (
                <select value={answers[n.key] ?? ''} onChange={(e) => setAnswers({ ...answers, [n.key]: e.target.value })}>
                  <option value="">Choose…</option>
                  <option value="yes">I agree</option>
                </select>
              ) : (
                <textarea
                  rows={2}
                  value={answers[n.key] ?? n.suggestion ?? ''}
                  onChange={(e) => setAnswers({ ...answers, [n.key]: e.target.value })}
                />
              )}
            </label>
          ))}
          <label className="ap-check">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Keep these answers for
            later applications
          </label>
          <button type="submit" className="btn btn-primary">Save answers and fill again</button>
        </form>
      )}
      {review && (
        <section className="ap-review" aria-label="What will be sent">
          <h3 className="kicker">What will be sent ({review.mode === 'ai' ? `filled by the AI agent${review.agent ? `, ${review.agent}` : ''}` : 'filled by a playbook'})</h3>
          {review.warnings.map((w) => (
            <p key={w} className="ap-muted">{w}</p>
          ))}
          <table className="ap-table">
            <tbody>
              {review.fields.map((f) => (
                <tr key={f.key}>
                  <th scope="row">{f.label}</th>
                  <td>{f.value}</td>
                  <td className="ap-muted">{f.source ? SOURCE_LABEL[f.source] ?? f.source : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {review.skipped.length > 0 && (
            <p className="ap-muted">Left blank (optional): {review.skipped.map((f) => f.label).join(', ')}</p>
          )}
        </section>
      )}
      {detail.hasScreenshot && (
        <figure className="ap-shot">
          <img src={`/api/apply/applications/${encodeURIComponent(detail.id)}/screenshot?v=${encodeURIComponent(detail.updatedAt)}`} alt="The filled application form" />
          <figcaption className="ap-muted">The form as filled, before anything was sent.</figcaption>
        </figure>
      )}
      {err && <p className="ap-error">{err}</p>}
      <div className="ap-actions">
        {canApprove && review && (
          <button type="button" className="btn btn-primary" onClick={() => void act(() => applyApi.approve(detail.id, review.digest))}>
            Approve and submit{review.submitLabel ? ` (“${review.submitLabel}”)` : ''}
          </button>
        )}
        {(detail.stage === 'failed' || detail.stage === 'needs_user') && (
          <button type="button" className="btn btn-quiet" onClick={() => void act(() => applyApi.retry(detail.id))}>
            Try again
          </button>
        )}
        <label className="field ap-inline">
          <span>Mark as</span>
          <select
            value=""
            onChange={(e) => {
              const stage = e.target.value as ApplicationStage;
              if (stage) void act(() => applyApi.stage(detail.id, stage));
            }}
          >
            <option value="">Choose…</option>
            {TRACKING_STAGES.filter((s) => s !== detail.stage).map((s) => (
              <option key={s} value={s}>
                {STAGE_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
        <a className="btn btn-quiet" href={detail.jobUrl} target="_blank" rel="noreferrer noopener">
          Job page
        </a>
        <button
          type="button"
          className="btn btn-quiet"
          onClick={async () => {
            try {
              await applyApi.remove(detail.id);
              onClose();
            } catch (error) {
              setErr(message(error));
            }
          }}
        >
          Delete
        </button>
      </div>
      <ol className="ap-events">
        {detail.events.map((e, i) => (
          <li key={`${e.at}-${i}`} className="ap-muted">
            {new Date(e.at).toLocaleString()} · {e.toStage ? STAGE_LABEL[e.toStage] : e.kind}
            {e.note ? ` · ${e.note}` : ''}
          </li>
        ))}
      </ol>
    </article>
  );
}

function EmailPanel({ onChanged }: { onChanged: () => void }) {
  const [subject, setSubject] = useState('');
  const [sender, setSender] = useState('');
  const [body, setBody] = useState('');
  const [result, setResult] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const got = await applyApi.email({ subject, sender, body });
      setResult(
        got.applied
          ? `Moved to ${STAGE_LABEL[got.stage as ApplicationStage] ?? got.stage}: “${got.quote ?? ''}”`
          : got.note ?? (got.stage === 'none' ? 'Nothing in it changes a stage.' : `Reads as ${got.stage}, but no application matched.`),
      );
      setBody('');
      onChanged();
    } catch (err) {
      setResult(message(err));
    }
  };
  return (
    <details className="panel ap-details">
      <summary className="ap-summary">Got an email from an employer? Paste it to update the stage</summary>
      <form onSubmit={submit} className="ap-form">
        <label className="field">
          <span>From</span>
          <input value={sender} onChange={(e) => setSender(e.target.value)} placeholder="no-reply@greenhouse.io" />
        </label>
        <label className="field">
          <span>Subject</span>
          <input value={subject} onChange={(e) => setSubject(e.target.value)} />
        </label>
        <label className="field ap-field-wide">
          <span>Email text (read once, never stored)</span>
          <textarea rows={4} value={body} onChange={(e) => setBody(e.target.value)} required />
        </label>
        <div className="ap-actions">
          <button type="submit" className="btn btn-quiet">Read it</button>
          {result && <span className="ap-muted">{result}</span>}
        </div>
      </form>
    </details>
  );
}

function MetricsPanel() {
  const [metrics, setMetrics] = useState<ApplyMetrics | null>(null);
  useEffect(() => {
    applyApi.metrics().then(setMetrics).catch(() => setMetrics(null));
  }, []);
  if (!metrics) return null;
  const pct = (n: number) => `${Math.round(n * 100)}%`;
  return (
    <details className="panel ap-details">
      <summary className="ap-summary">Engine metrics (admin): playbook vs AI, last 30 days</summary>
      <table className="ap-table">
        <thead>
          <tr>
            <th scope="col">ATS</th>
            <th scope="col">Runs</th>
            <th scope="col">Playbook</th>
            <th scope="col">AI</th>
            <th scope="col">AI share</th>
            <th scope="col">Success</th>
            <th scope="col">Cost</th>
          </tr>
        </thead>
        <tbody>
          {[...metrics.byAts, metrics.total].map((r) => (
            <tr key={r.ats}>
              <th scope="row">{r.ats}</th>
              <td className="numeral">{r.runs}</td>
              <td className="numeral">{r.playbookRuns}</td>
              <td className="numeral">{r.aiRuns}</td>
              <td className="numeral">{pct(r.aiShare)}</td>
              <td className="numeral">{pct(r.successRate)}</td>
              <td className="numeral">${(r.costMicroUsd / 1e6).toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {metrics.failureReasons.length > 0 && (
        <p className="ap-muted">Failure reasons: {metrics.failureReasons.map((f) => `${f.reason} (${f.count})`).join(', ')}</p>
      )}
      {metrics.playbooks.length > 0 && (
        <p className="ap-muted">
          Playbooks: {metrics.playbooks.map((p) => `${p.domain ?? p.ats} ${p.status} v${p.version} (${p.successes} ok)`).join('; ')}
        </p>
      )}
    </details>
  );
}
