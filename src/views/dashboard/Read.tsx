/**
 * The top of the dashboard, the person's own space:
 *
 *   Hello                the greeting for the hour and the day, then one line at a time from a theme (an achievement, a
 *                        roast, a next move, a did-you-know, the tip of the day, a quote; lines.ts builds them),
 *                        changing every few seconds and on "Another"; a line that rests on a skill opens that skill's
 *                        evidence in a drawer under it (the quote, its source, "This is wrong")
 *   usePreviousProfile   the analysis before this one, for what moved since
 *
 * The read's lines are written once, at analysis time, and stored on the profile; the rest of the lines are counted
 * from the records or come from the quotes and tips in lines.ts. It is the top of the one-screen Home: the greeting and the
 * line on the left, the share button and "Worth saying out loud" (`end`) on the right.
 */
import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { PracticeHistory } from '../../../shared/interview';
import type { Profile } from '../../../shared/types';
import { useMe } from '../../auth/useMe';
import { fetchProfile } from '../../lib/api';
import { skillAction } from '../../lib/prompts';
import { useMediaQuery } from '../../lib/ui';
import { actions, useApp } from '../../store';
import { THEME_LABEL, deskLines, localDay } from './lines';
import type { Achievement } from './model';
import { useDeskNotes } from './notes';

/* ───────────────────────── the greeting and the read ───────────────────────── */

const ROTATE_MS = 9000;

function greeting(now: Date, name: string): string {
  const h = now.getHours();
  const word = h < 5 ? 'Still up' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
  const first = name !== 'You' ? name.trim().split(/\s+/)[0] : '';
  return first ? `${word}, ${first}.` : `${word}.`;
}

export function Hello({ profile, shelf, history, end }: { profile: Profile; shelf: readonly Achievement[]; history: PracticeHistory | null; end?: ReactNode }) {
  const skills = useMemo(() => new Map(profile.skills.map((s) => [s.id, s])), [profile.skills]);
  const sources = useMemo(() => new Map(profile.sources.map((s) => [s.id, s])), [profile.sources]);
  const domains = useMemo(() => new Map(profile.domains.map((d) => [d.id, d])), [profile.domains]);
  const today = useMemo(() => new Date(), []);
  // today's lines, the themes taking turns; never empty (the quotes and the tip need no data)
  const lines = useMemo(() => deskLines({ profile, shelf, history, day: localDay(today.getTime()) }), [profile, shelf, history, today]);

  const [at, setAt] = useState(() => Math.floor(Date.now() / ROTATE_MS) % Math.max(1, lines.length));
  const [paused, setPaused] = useState(false);
  const [open, setOpen] = useState<{ skillId: string; at: number } | null>(null);
  const still = useMediaQuery('(prefers-reduced-motion: reduce)');
  const desk = useDeskNotes(profile.id);

  // a new profile starts over
  useEffect(() => {
    setAt(0);
    setOpen(null);
  }, [profile.id]);

  // the lines take turns; not while the person hovers, has the evidence open, or asked for no motion
  useEffect(() => {
    if (lines.length < 2 || paused || open || still) return;
    const id = window.setInterval(() => setAt((i) => (i + 1) % lines.length), ROTATE_MS);
    return () => window.clearInterval(id);
  }, [lines.length, paused, open, still]);

  const line = lines[at % lines.length];
  const skill = open ? skills.get(open.skillId) : undefined;
  const evidence = skill && skill.evidence.length ? skill.evidence[open!.at % skill.evidence.length] : undefined;
  const source = evidence ? sources.get(evidence.sourceId) : undefined;
  const lineSkill = line.skillId ? skills.get(line.skillId) : undefined;
  const cited = !!lineSkill && lineSkill.evidence.length > 0;

  const next = () => {
    setOpen(null);
    setAt((i) => (i + 1) % lines.length);
  };
  const wrong = () => {
    if (!skill) return;
    const a = skillAction(skill, domains.get(skill.domainId)?.name ?? 'Skill');
    actions.openChat(
      a.origin,
      `My dashboard says “${line.text}”, backed by “${evidence?.text ?? ''}”. That isn't right. Ask me what is, then tell me how the graph should change and what evidence would support it.`,
    );
  };
  const tell = () =>
    actions.openChat(
      { kind: 'you', title: profile.person.currentRole, caption: 'Your read' },
      "One of the lines on my dashboard doesn't sound like me. Ask me which, then tell me which evidence it came from and how to put it right.",
    );

  return (
    <header className="os-top" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)} onFocus={() => setPaused(true)} onBlur={() => setPaused(false)}>
      <div className="os-top-text">
        <p className="kicker">
          {[today.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' }), profile.person.currentRole, profile.person.location?.split(',')[0]].filter(Boolean).join(' · ')}
        </p>
        <h1 className="serif os-h1">{greeting(today, profile.person.name)}</h1>
      </div>
      {end && <div className="os-top-end">{end}</div>}

      {/* one line at a time: its theme in small print, the sentence, who said it */}
      <div className="os-line" aria-live="polite">
        <span key={`theme-${line.id}`} className="kicker os-line-k">
          {THEME_LABEL[line.theme]}
        </span>
        {cited ? (
          <button
            key={line.id}
            type="button"
            className={`serif os-line-t os-line-btn${open?.skillId === line.skillId ? ' is-open' : ''}`}
            aria-expanded={open?.skillId === line.skillId}
            aria-controls="db-evidence"
            title="Press the line for its evidence"
            onClick={() => setOpen(open?.skillId === line.skillId ? null : { skillId: line.skillId!, at: 0 })}
          >
            {line.text}
          </button>
        ) : (
          <p key={line.id} className={`serif os-line-t${line.theme === 'quote' ? ' is-quote' : ''}`}>
            {line.text}
          </p>
        )}
        {line.note && (
          <span key={`note-${line.id}`} className="fine os-line-note">
            {line.note}
          </span>
        )}
        <span className="os-line-acts">
          {line.origin === 'read' && (
            <button type="button" className="btn btn-sm btn-quiet" aria-label="Doesn't sound like you? Tell Leafout" title="Doesn't sound like you? Tell Leafout" onClick={tell}>
              Not you?
            </button>
          )}
          <button
            type="button"
            className="btn btn-sm btn-quiet"
            aria-pressed={desk.isPinned(line.text)}
            onClick={() => desk.togglePin({ text: line.text, label: THEME_LABEL[line.theme], by: line.theme === 'quote' ? line.note : undefined })}
          >
            {desk.isPinned(line.text) ? 'Pinned ✓' : 'Pin it'}
          </button>
          {lines.length > 1 && (
            <button type="button" className="btn btn-sm" onClick={next} aria-label="Another line">
              Another →
            </button>
          )}
        </span>

        {skill && evidence && (
          <div className="panel db-drawer" id="db-evidence" role="region" aria-label={`Evidence for ${skill.name}`}>
            <div className="db-drawer-text">
              <span className="kicker">
                Evidence · {skill.evidence.length > 1 ? `${(open!.at % skill.evidence.length) + 1} of ${skill.evidence.length} · ` : ''}
                {source?.label ?? evidence.sourceId}
                {source?.sub ? ` · ${source.sub}` : ''}
              </span>
              <p className="db-drawer-q serif">“{evidence.text}”</p>
              <span className="db-cite">
                {skill.name} · <span className="numeral">{skill.score}</span>/100 · {domains.get(skill.domainId)?.name ?? ''}
              </span>
            </div>
            <div className="db-drawer-actions">
              {source?.url && (
                <a className="btn btn-sm" href={source.url} target="_blank" rel="noopener noreferrer">
                  Open the source
                </a>
              )}
              {skill.evidence.length > 1 && (
                <button type="button" className="btn btn-sm btn-quiet" onClick={() => setOpen({ skillId: skill.id, at: open!.at + 1 })}>
                  Next citation →
                </button>
              )}
              <button type="button" className="btn btn-sm btn-quiet" onClick={wrong}>
                This is wrong
              </button>
              <button type="button" className="btn btn-sm btn-quiet" aria-label="Close the evidence" onClick={() => setOpen(null)}>
                Close
              </button>
            </div>
          </div>
        )}
      </div>
    </header>
  );
}


/* ───────────────────────── the analysis before this one ───────────────────────── */

const isSample = (id: string) => id.startsWith('sample-');

/**
 * The analysis before this one, when there is one to compare with: the one this browser replaced (the store keeps it),
 * else the account's newest older profile from the server. Null for a first analysis and for the sample.
 */
export function usePreviousProfile(profile: Profile): Profile | null {
  const kept = useApp((s) => s.previous);
  const { profiles } = useMe();
  const [fetched, setFetched] = useState<Profile | null>(null);
  const at = Date.parse(profile.createdAt);
  const samePerson = (a: Profile, b: Profile) => a.person.name === 'You' || b.person.name === 'You' || a.person.name.trim().toLowerCase() === b.person.name.trim().toLowerCase();
  const local =
    kept && kept.id !== profile.id && !isSample(kept.id) && !isSample(profile.id) && Date.parse(kept.createdAt) < at && samePerson(kept, profile) ? kept : null;
  const olderId = useMemo(() => {
    if (local || isSample(profile.id)) return null;
    const older = profiles
      .filter((p) => p.id !== profile.id && !isSample(p.id) && Date.parse(p.createdAt) < at)
      .sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt))[0];
    return older?.id ?? null;
  }, [local, profiles, profile.id, at]);
  useEffect(() => {
    if (!olderId) {
      setFetched(null);
      return;
    }
    let on = true;
    fetchProfile(olderId)
      .then((p) => {
        if (on) setFetched(p);
      })
      .catch(() => {
        if (on) setFetched(null);
      });
    return () => {
      on = false;
    };
  }, [olderId]);
  return local ?? fetched;
}
