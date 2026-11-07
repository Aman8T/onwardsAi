/**
 * "Apply" on a posting match: asks for the application link (a pasted posting has none), starts an application that keeps
 * the match, and opens it in the tracker. Renders nothing while auto-apply is off. Links found in the posting are offered as
 * text to pick, never opened by us.
 */
import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { applyApi, ApplyError } from './api';

const ATS_LINK = /(greenhouse\.io|lever\.co|ashbyhq\.com|myworkdayjobs\.com|\/careers?\/|\/jobs?\/)/i;

export function ApplyFromMatch({ matchId, links = [] }: { matchId?: string; links?: readonly string[] }) {
  const [enabled, setEnabled] = useState(false);
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState('');
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    applyApi.status().then((s) => setEnabled(s.enabled)).catch(() => setEnabled(false));
  }, []);
  if (!enabled || !matchId) return null;
  const suggestions = links.filter((l) => ATS_LINK.test(l)).slice(0, 3);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      const made = await applyApi.create(url.trim(), matchId);
      window.location.assign(`/apply?id=${encodeURIComponent(made.id)}`);
    } catch (error) {
      setErr(error instanceof ApplyError ? error.message : 'Could not start the application.');
    }
  };
  if (!open) {
    return (
      <button type="button" className="btn btn-quiet mt-btn" onClick={() => setOpen(true)}>
        Apply
      </button>
    );
  }
  return (
    <form className="ap-from-match" onSubmit={submit}>
      <label className="field">
        <span>The application link for this role</span>
        <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://jobs.lever.co/…" required />
      </label>
      {suggestions.length > 0 && (
        <p className="ap-muted">
          Links in the posting:{' '}
          {suggestions.map((s) => (
            <button key={s} type="button" className="btn btn-quiet btn-sm" onClick={() => setUrl(s)}>
              {s.replace(/^https?:\/\//, '').slice(0, 48)}
            </button>
          ))}
        </p>
      )}
      <button type="submit" className="btn btn-primary mt-btn">Fill it for my review</button>
      {err && <p className="ap-error">{err}</p>}
    </form>
  );
}
