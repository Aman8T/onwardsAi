/**
 * The skill graph as the biggest thing on the dashboard: the radar with role mastery in its centre and one floating
 * callout per domain (the score large, the domain's name, a level chip), each at the end of a thin leader line from its
 * data point, with a ring round the point. The labels are real HTML buttons over the SVG, never SVG text; where they sit
 * is computed from each axis's angle (radarLayout.ts), so any number of domains lays out the same way.
 *
 * Pressing a callout does what pressing an axis does on the graph screen: a domain opens into its skills (the radar
 * becomes that domain's skills, with a way back, and Escape goes up one level), and a skill opens its popover with the one
 * action that seeds the chat. The centre opens "You" (role mastery), or the domain's own panel once drilled. Measured
 * things stay straight and plain: no pen, no paper here.
 */
import { AnimatePresence } from 'motion/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { MouseEvent as ReactMouseEvent } from 'react';
import { createPortal } from 'react-dom';
import type { Domain, Profile } from '../../../shared/types';
import { Popover } from '../../components/Popover';
import type { PopoverModel } from '../../components/Popover';
import { TIER_LABEL, tier } from '../../lib/layout';
import { domainAction, skillAction, youAction } from '../../lib/prompts';
import type { NodeAction } from '../../lib/prompts';
import { NARROW_QUERY, clamp, plural, useMediaQuery } from '../../lib/ui';
import { dayLabel } from '../../discover/prepare/words';
import { practisedFor, usePractised } from '../../practice/practised';
import { VERDICT_LABEL } from '../../practice/words';
import { actions, useApp } from '../../store';
import { HERO_H, HERO_W, heroLayout } from './radarLayout';

const FALLBACK_DOMAIN: Domain = { id: 'general', name: 'General', short: 'General', score: 50, summary: '' };
const POP_W = 304;

interface Sel {
  kind: 'skill' | 'domain' | 'you';
  id: string;
}

export function HeroRadar({ profile }: { profile: Profile }) {
  const flipped = useApp((s) => s.flipped);
  const narrow = useMediaQuery(NARROW_QUERY);
  const practised = usePractised(profile.id);
  const domains = profile.domains.length ? profile.domains : [FALLBACK_DOMAIN];
  const [domainId, setDomainId] = useState<string | null>(null);
  const [sel, setSel] = useState<Sel | null>(null);
  const [focusPop, setFocusPop] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const restore = useRef<HTMLElement | null>(null);
  const anchor = useRef<string | null>(null);

  // a new profile starts at the top level
  useEffect(() => {
    setDomainId(null);
    setSel(null);
  }, [profile.id]);

  const drilled = domainId ? (domains.find((d) => d.id === domainId) ?? null) : null;
  const skills = useMemo(() => (drilled ? profile.skills.filter((s) => (domains.find((d) => d.id === s.domainId) ?? domains[0]).id === drilled.id) : []), [drilled, profile.skills, domains]);
  const axes = useMemo(
    () => (drilled ? skills.map((s) => ({ id: s.id, label: s.name, value: s.score })) : domains.map((d) => ({ id: d.id, label: d.name, value: d.score }))),
    [drilled, skills, domains],
  );
  const layout = useMemo(() => heroLayout(axes), [axes]);
  const chart = axes.length >= 3;

  const clear = useCallback((restoreFocus = false) => {
    setSel(null);
    if (restoreFocus) restore.current?.focus({ preventScroll: true });
  }, []);

  const goUp = useCallback(() => {
    setDomainId(null);
    setSel(null);
  }, []);

  // Escape closes the popover first, then goes up a level
  useEffect(() => {
    if (flipped || (!sel && !drilled)) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      e.preventDefault();
      if (sel) clear(true);
      else goUp();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [sel, drilled, flipped, clear, goUp]);

  const press = (id: string, e: ReactMouseEvent<HTMLElement>) => {
    if (!drilled) {
      setDomainId(id);
      setSel(null);
      return;
    }
    restore.current = e.currentTarget;
    anchor.current = id;
    setFocusPop(e.detail === 0);
    setSel((cur) => (cur && cur.kind === 'skill' && cur.id === id ? null : { kind: 'skill', id }));
  };
  const pressCentre = (e: ReactMouseEvent<HTMLElement>) => {
    restore.current = e.currentTarget;
    anchor.current = null;
    setFocusPop(e.detail === 0);
    const next: Sel = drilled ? { kind: 'domain', id: drilled.id } : { kind: 'you', id: 'you' };
    setSel((cur) => (cur && cur.kind === next.kind && cur.id === next.id ? null : next));
  };

  /** Beside the callout that opened it (the centre's goes to the right of the centre), kept near the chart. */
  const place = useCallback(
    (h: number) => {
      const el = box.current;
      const W = el?.clientWidth ?? 0;
      const H = el?.clientHeight ?? 0;
      const c = layout.callouts.find((k) => k.id === anchor.current);
      if (!c) return { left: Math.round(clamp(W / 2 + 70, 0, Math.max(0, W - POP_W))), top: Math.round(clamp(H / 2 - h / 2, 0, Math.max(0, H - h))) };
      const x = (c.left / 100) * W;
      const y = (c.top / 100) * H;
      const left = c.right ? x - POP_W - 12 : x + 12;
      return { left: Math.round(clamp(left, -40, Math.max(0, W - POP_W + 40))), top: Math.round(clamp(y - h / 2, 0, Math.max(0, H - h))) };
    },
    [layout.callouts],
  );

  const pop = useMemo<{ model: PopoverModel; action: NodeAction } | null>(() => {
    if (!sel) return null;
    const key = `${sel.kind}:${sel.id}`;
    if (sel.kind === 'skill') {
      const s = profile.skills.find((k) => k.id === sel.id);
      if (!s) return null;
      const home = domains.find((d) => d.id === s.domainId) ?? domains[0];
      const action = skillAction(s, home.name);
      const p = practisedFor(
        s.evidence.map((ev) => ev.text),
        practised,
      );
      return {
        action,
        model: {
          key,
          kicker: home.name,
          title: s.name,
          score: s.score,
          badge: TIER_LABEL[tier(s.score)],
          bullets: s.evidence.slice(0, 3).map((ev) => ({ tag: profile.sources.find((x) => x.id === ev.sourceId)?.label ?? ev.sourceId, text: ev.text })),
          foot: s.unlocks.length ? { label: 'Unlocks', value: s.unlocks.join(' · ') } : undefined,
          practised: p ? `${VERDICT_LABEL[p.best]} on ${dayLabel(p.on.slice(0, 10))}` : undefined,
          cta: action.cta,
        },
      };
    }
    if (sel.kind === 'domain') {
      const d = domains.find((x) => x.id === sel.id);
      if (!d) return null;
      const action = domainAction(d);
      return { action, model: { key, kicker: `Domain · ${plural(skills.length, 'skill')}`, title: d.name, score: d.score, badge: TIER_LABEL[tier(d.score)], summary: d.summary || undefined, cta: action.cta } };
    }
    const ranked = [...profile.skills].sort((a, b) => b.score - a.score);
    const top = ranked.slice(0, 2);
    const low = ranked
      .slice(-2)
      .reverse()
      .filter((s) => !top.includes(s));
    const action = youAction(profile);
    return {
      action,
      model: {
        key,
        kicker: `You · ${profile.person.currentRole || 'today'}`,
        title: 'Role mastery',
        score: profile.mastery.score,
        badge: TIER_LABEL[tier(profile.mastery.score)],
        bullets: [
          ...(top.length ? [{ tag: 'Strongest', text: top.map((s) => `${s.name} ${s.score}`).join(' · ') }] : []),
          ...(low.length ? [{ tag: 'Holding back', text: low.map((s) => `${s.name} ${s.score}`).join(' · ') }] : []),
        ],
        cta: action.cta,
      },
    };
  }, [sel, profile, domains, skills.length, practised]);

  const popover = (sheet: boolean) =>
    pop && <Popover key={pop.model.key} model={pop.model} place={sheet ? undefined : place} sheet={sheet} autoFocus={focusPop ? 'dialog' : false} onClose={() => clear(true)} onAct={() => actions.openChat(pop.action.origin, pop.action.question)} />;

  const role = profile.person.currentRole || 'professional';
  const centreScore = drilled ? drilled.score : profile.mastery.score;
  const label = drilled
    ? `Skill radar: ${plural(skills.length, 'skill')} in ${drilled.name}. ${drilled.name} ${drilled.score} of 100.`
    : `Skill radar: ${axes.map((a) => `${a.label} ${a.value}`).join(', ')}. Role mastery ${profile.mastery.score} of 100.`;

  return (
    <div className="rz-wrap">
      {drilled && (
        <button type="button" className="rz-up btn btn-sm btn-quiet" onClick={goUp}>
          ← All domains
        </button>
      )}
      <div className={`rz${chart ? '' : ' is-list'}`} ref={box} data-level={drilled ? 1 : 0}>
        <div className="rz-plot">
        {chart && (
          <svg viewBox={`0 0 ${HERO_W} ${HERO_H}`} role="img" aria-label={label}>
            {layout.rings.map((d, i) => (
              <path key={i} className="rz-ring" d={d} />
            ))}
            {layout.spokes.map((p, i) => (
              <line key={i} className="rz-spoke" x1={300} y1={215} x2={p.x} y2={p.y} />
            ))}
            <path className="rz-shape" d={layout.shape} />
            <path className="lead" d={layout.leaders} />
            {layout.callouts.map((c) => (
              <circle key={`h${c.id}`} className="halo" cx={c.px} cy={c.py} r={9} />
            ))}
            {layout.callouts.map((c) => (
              <circle key={`d${c.id}`} className="rz-dot" cx={c.px} cy={c.py} r={3.5} />
            ))}
          </svg>
        )}
        <button type="button" className="co-mid" aria-pressed={sel?.kind === 'you' || sel?.kind === 'domain'} aria-label={drilled ? `${drilled.name}, domain score ${centreScore}` : `You, ${role}, role mastery ${centreScore}`} onClick={pressCentre}>
          <b className="numeral" aria-hidden="true">
            {centreScore}
          </b>
          <span className="fine">{drilled ? (drilled.short || drilled.name) : 'role mastery'}</span>
        </button>
        </div>
        <div className="rz-labels">
        {layout.callouts.map((c, i) => {
          const a = axes[i];
          const t = tier(a.value);
          return (
            <button
              key={c.id}
              type="button"
              className={`co${c.right ? ' r' : ''}${chart ? '' : ' is-flow'}`}
              style={chart ? { left: `${c.left}%`, top: `${c.top}%` } : undefined}
              aria-pressed={drilled ? sel?.kind === 'skill' && sel.id === c.id : undefined}
              aria-haspopup={drilled ? 'dialog' : undefined}
              aria-label={`${a.label}, ${a.value} out of 100, ${TIER_LABEL[t]}${drilled ? '' : '. Opens its skills'}`}
              onClick={(e) => press(c.id, e)}
            >
              <span className="co-n numeral" aria-hidden="true">
                {a.value}
              </span>
              <span className="co-l" aria-hidden="true">
                {a.label}
              </span>
              <span className={`chip${t === 'expert' ? ' is-on' : t === 'gap' ? ' is-gap' : ''}`} aria-hidden="true">
                {TIER_LABEL[t]}
              </span>
            </button>
          );
        })}
        </div>
        <AnimatePresence>{!narrow && popover(false)}</AnimatePresence>
      </div>
      {narrow && createPortal(<AnimatePresence>{!flipped && popover(true)}</AnimatePresence>, document.body)}
      <table className="sr-only">
        <caption>{drilled ? `Skills in ${drilled.name}, 0 to 100` : 'Domain scores, 0 to 100'}</caption>
        <tbody>
          {axes.map((a) => (
            <tr key={a.id}>
              <th scope="row">{a.label}</th>
              <td>{a.value}</td>
              <td>{TIER_LABEL[tier(a.value)]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
