/**
 * The top of the dashboard (boards "Dashboard idea 1" and "idea 2" on the all-screens canvas):
 *
 *   Read          the three or four sentences written from the graph; every cited clause is a dotted button that opens
 *                 that skill's evidence in a drawer under the paragraph (the quote, its source, "This is wrong")
 *   NotOnResume   the strongest things the other sources proved that the resume never mentions, with the one action
 *   SinceLast     what moved since the previous analysis, when there is one to compare with
 *   AskStrip      one field and four questions worth asking about yourself; each opens the chat with that question
 *
 * The read is written once, at analysis time, and stored on the profile; everything else here is counted in code.
 */
import { useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import type { Profile } from '../../../shared/types';
import { useMe } from '../../auth/useMe';
import { fetchProfile } from '../../lib/api';
import { skillAction, sourceAction } from '../../lib/prompts';
import { plural } from '../../lib/ui';
import { actions, useApp } from '../../store';
import { askQuestions, citedCount, deltas, notOnResume } from './model';

/* ───────────────────────── the read ───────────────────────── */

export function Read({ profile }: { profile: Profile }) {
  const skills = useMemo(() => new Map(profile.skills.map((s) => [s.id, s])), [profile.skills]);
  const sources = useMemo(() => new Map(profile.sources.map((s) => [s.id, s])), [profile.sources]);
  const domains = useMemo(() => new Map(profile.domains.map((d) => [d.id, d])), [profile.domains]);
  const [open, setOpen] = useState<{ skillId: string; at: number } | null>(null);
  // a new profile closes the drawer
  useEffect(() => setOpen(null), [profile.id]);

  const read = profile.read ?? [];
  if (read.length === 0) {
    return (
      <header className="db-greet">
        <span className="kicker">Dashboard</span>
        <h1 className="title db-h1">Where you stand, and what is moving.</h1>
      </header>
    );
  }

  const skill = open ? skills.get(open.skillId) : undefined;
  const evidence = skill && skill.evidence.length ? skill.evidence[open!.at % skill.evidence.length] : undefined;
  const source = evidence ? sources.get(evidence.sourceId) : undefined;
  const clause = open ? read.find((s) => s.skillId === open.skillId)?.text.trim() : undefined;
  const cited = citedCount(profile);

  const wrong = () => {
    if (!skill) return;
    const a = skillAction(skill, domains.get(skill.domainId)?.name ?? 'Skill');
    actions.openChat(
      a.origin,
      `My read says “${clause ?? skill.name}”, backed by “${evidence?.text ?? ''}”. That isn't right. Ask me what is, then tell me how the graph should change and what evidence would support it.`,
    );
  };
  const tell = () =>
    actions.openChat(
      { kind: 'you', title: profile.person.currentRole, caption: 'Your read' },
      "Something in my read doesn't sound like me. Ask me which part, then tell me which evidence it came from and how to put it right.",
    );

  return (
    <header className="db-greet db-readwrap">
      <span className="kicker">
        {[profile.person.name !== 'You' ? profile.person.name : null, profile.person.currentRole, profile.person.location?.split(',')[0]].filter(Boolean).join(' · ')} ·{' '}
        <span className="db-kicker-strong">your read</span>, from {plural(profile.sources.filter((s) => s.ok).length, 'source')}
      </span>
      <p className="db-read serif">
        {read.map((seg, i) => {
          const s = seg.skillId ? skills.get(seg.skillId) : undefined;
          if (!s || s.evidence.length === 0) return <span key={i}>{seg.text}</span>;
          const isOpen = open?.skillId === s.id;
          return (
            <button
              key={i}
              type="button"
              className={`db-ev${isOpen ? ' is-open' : ''}`}
              aria-expanded={isOpen}
              aria-controls="db-evidence"
              onClick={() => setOpen(isOpen ? null : { skillId: s.id, at: 0 })}
            >
              {seg.text}
            </button>
          );
        })}
      </p>

      {skill && evidence && (
        <div className="panel db-drawer" id="db-evidence" role="region" aria-label={`Evidence for ${skill.name}`}>
          <div className="db-drawer-text">
            <span className="kicker">
              Evidence for “{clause}” · {skill.evidence.length > 1 ? `${(open!.at % skill.evidence.length) + 1} of ${skill.evidence.length} · ` : ''}
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

      <div className="db-readmeta">
        <span className="kicker">
          {cited > 0 ? `Every underlined clause opens its evidence · ${plural(cited, 'citation')}` : 'Written from your evidence'}
        </span>
        <button type="button" className="db-link" onClick={tell}>
          Doesn't sound like you? Tell Leafout
        </button>
      </div>
    </header>
  );
}

/* ───────────────────────── not on your resume ───────────────────────── */

export function NotOnResume({ profile }: { profile: Profile }) {
  const found = useMemo(() => notOnResume(profile), [profile]);
  if (!found) return null;
  const { resume, items, role } = found;
  const names = items.map((it) => it.skill.name);
  const add = () => {
    const a = sourceAction(resume);
    actions.openChat(
      a.origin,
      `My resume doesn't mention ${names.join(' or ')}, which ${items.map((it) => it.source.label).filter((v, i, arr) => arr.indexOf(v) === i).join(' and ')} prove${items.length === 1 ? 's' : ''}. Help me add ${items.length === 1 ? 'it' : 'them'} as resume bullets in my own voice, using only the cited facts.`,
    );
  };
  return (
    <section className="db-surprise" aria-labelledby="db-surprise-h">
      <p className="db-small db-surprise-text">
        <b id="db-surprise-h">Not on your resume.</b>{' '}
        {items.map((it, i) => (
          <span key={it.skill.id}>
            {i > 0 ? ' and ' : ''}
            {it.skill.name} ({it.source.label}: “{it.evidence.text}”)
          </span>
        ))}
        .{role ? ` ${items.length === 1 ? 'It counts' : 'Both count'} towards ${role.title}; ${items.length === 1 ? 'it appears' : 'neither appears'} in the resume you uploaded.` : ''}
      </p>
      <div className="db-surprise-chips">
        {items
          .map((it) => it.source)
          .filter((s, i, arr) => arr.indexOf(s) === i)
          .map((s) => (
            <span key={s.id} className="chip">
              {s.label}
              {s.sub ? ` · ${s.sub}` : ''}
            </span>
          ))}
        <span className="chip is-gap">resume · not mentioned</span>
      </div>
      <button type="button" className="btn btn-sm db-surprise-act" onClick={add}>
        Add {items.length === 1 ? 'it' : 'both'} to my resume
      </button>
    </section>
  );
}

/* ───────────────────────── since last time ───────────────────────── */

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

function sinceLabel(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return 'last time';
  return new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'long' });
}

export function SinceLast({ profile, previous }: { profile: Profile; previous: Profile | null }) {
  const moved = useMemo(() => (previous ? deltas(previous, profile) : []), [previous, profile]);
  if (!previous || moved.length === 0) return null;
  return (
    <section className="db-since" aria-labelledby="db-since-h">
      <div className="db-card-head">
        <h2 className="kicker" id="db-since-h">
          Since {sinceLabel(previous.createdAt)}
        </h2>
        <span className="kicker">{plural(profile.sources.filter((s) => s.ok).length, 'source')} · analysed {sinceLabel(profile.createdAt)}</span>
      </div>
      <ul className="db-deltas">
        {moved.map((d) => (
          <li key={d.label} className="db-delta">
            <span className="numeral db-delta-n">
              {d.was ? (
                <>
                  <span className="db-delta-was">{d.was}</span> → {d.now}
                </>
              ) : (
                d.now
              )}
            </span>
            <span className="db-small">{d.label}</span>
            <span className="db-fine">{d.note}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ───────────────────────── ask about yourself ───────────────────────── */

export function AskStrip({ profile }: { profile: Profile }) {
  const questions = useMemo(() => askQuestions(profile), [profile]);
  const [text, setText] = useState('');
  // a free thread each time: the same question asked twice is two threads, and the existing "you" thread is left alone
  const ask = (q: string) => actions.openChat({ kind: 'free', title: 'About you', caption: 'Asked from the dashboard' }, q);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const q = text.trim();
    if (!q) return;
    setText('');
    ask(q);
  };
  return (
    <section className="db-ask" aria-label="Ask about yourself">
      <form className="db-ask-form" onSubmit={submit}>
        <label className="kicker db-ask-label" htmlFor="db-ask-field">
          Ask about yourself
        </label>
        <input
          id="db-ask-field"
          className="field db-ask-field"
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Anything your graph can answer, with the evidence…"
          autoComplete="off"
        />
      </form>
      <div className="db-ask-qs">
        {questions.map((q) => (
          <button key={q} type="button" className="db-ask-q" onClick={() => ask(q)}>
            {q}
          </button>
        ))}
      </div>
    </section>
  );
}
