/**
 * View 1 — the current-role skill radar (CalmNow / CalmNowSkills boards).
 *
 * Rail on the left: profile, role mastery, the domain list and the tier legend.
 * Centre: one serif line, then the radar — domain axes at level 0; choosing a
 * domain morphs the same radar into that domain's skills (level 1, with a quiet
 * breadcrumb back). A skill vertex, a source chip, the centre numeral and the
 * rail's mastery button each open one popover with exactly one action that
 * seeds the chat. Hovering a skill vertex tints the source chips that evidence
 * it (this replaces the old edge highlight).
 */
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { MouseEvent as ReactMouseEvent } from 'react';
import { createPortal } from 'react-dom';
import type { Domain, Profile, Skill, SourceSummary } from '../../../shared/types';
import { Popover } from '../../components/Popover';
import type { PopoverBullet, PopoverModel } from '../../components/Popover';
import { Radar } from '../../components/Radar';
import type { RadarAxis, RadarHandle, VertexGeom } from '../../components/Radar';
import { ValueLine } from '../../components/ValueLine';
import { PortfolioInvite } from './PortfolioInvite';
import { placePopover, tier, TIER_LABEL } from '../../lib/layout';
import { domainAction, skillAction, sourceAction, youAction } from '../../lib/prompts';
import type { NodeAction } from '../../lib/prompts';
import { EASE, NARROW_QUERY, clamp, plural, useElementSize, useMediaQuery, useTween } from '../../lib/ui';
import { actions, useApp } from '../../store';
import '../../styles/now.css';

/* ───────────────────────── types and helpers ───────────────────────── */

type SelKind = 'skill' | 'domain' | 'source' | 'you';
interface Sel {
  kind: SelKind;
  id: string;
}
const keyOf = (kind: SelKind, id: string) => `${kind}:${id}`;

/** Where the open popover is anchored: a radar vertex (in wrapper px) or the element that was activated. */
interface Anchor {
  geom?: VertexGeom;
  el?: HTMLElement | null;
}

interface ViewState {
  /** The drilled domain (level 1), or null for all domains (level 0). */
  domainId: string | null;
  /** The axis the radar crossfade pivots on; set right before a level change. */
  pivotId: string | null;
}

/** Where focus lands after a level change: an axis or list row by id, or the breadcrumb. */
type FocusTarget = { kind: 'axis'; id: string } | { kind: 'crumb' };

interface NowPop {
  model: PopoverModel;
  action: NodeAction;
  place: (height: number) => { left: number; top: number };
}

interface NowSource extends SourceSummary {
  /** Skill ids this source provides evidence for. */
  links: string[];
}

interface NowModel {
  domains: Domain[];
  skills: Skill[];
  sources: NowSource[];
  domainById: Map<string, Domain>;
  skillById: Map<string, Skill>;
  sourceById: Map<string, NowSource>;
  anySource: Map<string, SourceSummary>;
  skillsByDomain: Map<string, Skill[]>;
  sourcesBySkill: Map<string, NowSource[]>;
  homeOf: (s: Skill) => Domain;
}

const POP_W = 304;
const PAD = 8;
/** Chart column width from which the domain panel sits beside the radar instead of below it. */
const WIDE_CHART = 980;
const NONE: Skill[] = [];
const FALLBACK_DOMAIN: Domain = { id: 'general', name: 'General', short: 'General', score: 50, summary: '' };

/** Profiles whose reveal (radar draw + count-up) already played in this page session. */
const revealed = new Set<string>();

const article = (word: string) => (/^[aeio]/i.test(word.trim()) ? 'an' : 'a');
const cx = (...parts: Array<string | false | null | undefined>) => parts.filter(Boolean).join(' ');

function buildModel(profile: Profile): NowModel {
  const domains = profile.domains.length ? profile.domains : [FALLBACK_DOMAIN];
  const domainById = new Map(domains.map((d) => [d.id, d]));
  const skillById = new Map(profile.skills.map((s) => [s.id, s]));
  const anySource = new Map(profile.sources.map((s) => [s.id, s]));
  // Orphan skills (unknown domainId) attach to the first domain.
  const homeOf = (s: Skill): Domain => domainById.get(s.domainId) ?? domains[0];
  const skillsByDomain = new Map<string, Skill[]>(domains.map((d) => [d.id, []]));
  for (const s of profile.skills) skillsByDomain.get(homeOf(s).id)?.push(s);
  const sources: NowSource[] = profile.sources
    .filter((s) => s.ok)
    .map((s) => ({ ...s, links: profile.skills.filter((k) => k.evidence.some((e) => e.sourceId === s.id)).map((k) => k.id) }));
  const sourceById = new Map(sources.map((s) => [s.id, s]));
  const sourcesBySkill = new Map<string, NowSource[]>();
  for (const src of sources) for (const id of src.links) sourcesBySkill.set(id, [...(sourcesBySkill.get(id) ?? []), src]);
  return { domains, skills: profile.skills, sources, domainById, skillById, sourceById, anySource, skillsByDomain, sourcesBySkill, homeOf };
}

/* ───────────────────────── component ───────────────────────── */

export function NowGraph({ profile }: { profile: Profile }) {
  const flipped = useApp((s) => s.flipped);
  const reduced = useReducedMotion() ?? false;
  const narrow = useMediaQuery(NARROW_QUERY);

  // The reveal plays once per profile per page session; later mounts settle in at once.
  const [firstMount] = useState(() => !revealed.has(profile.id));
  useEffect(() => {
    revealed.add(profile.id);
  }, [profile.id]);
  const cinematic = firstMount && !reduced;

  const M = useMemo(() => buildModel(profile), [profile]);
  const role = profile.person.currentRole || 'professional';
  const mastery = useTween(profile.mastery.score, { enabled: cinematic });

  /* level */
  const [view, setView] = useState<ViewState>({ domainId: null, pivotId: null });
  const drilled = view.domainId ? (M.domainById.get(view.domainId) ?? null) : null;
  const drilledSkills = drilled ? (M.skillsByDomain.get(drilled.id) ?? NONE) : NONE;

  /* selection */
  const [sel, setSel] = useState<Sel | null>(null);
  const [focusPop, setFocusPop] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const restore = useRef<(() => void) | null>(null);
  const anchor = useRef<Anchor>({});
  const pendingFocus = useRef<FocusTarget | null>(null);
  const radarRef = useRef<RadarHandle>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  const crumbRef = useRef<HTMLButtonElement>(null);
  const [chartRef, chartSize] = useElementSize<HTMLElement>();
  const wide = chartSize.width >= WIDE_CHART;

  const isSel = (kind: SelKind, id: string) => !!sel && sel.kind === kind && sel.id === id;

  const select = (next: Sel, o: { viaKeyboard: boolean; restore: () => void; anchor: Anchor }) => {
    restore.current = o.restore;
    anchor.current = o.anchor;
    setFocusPop(o.viaKeyboard); // keyboard activation → move focus into the popover
    setSel((cur) => (cur && cur.kind === next.kind && cur.id === next.id ? null : next));
  };

  /** Select from a click on an element: the trigger gets focus back when the popover closes. */
  const selectFrom = (next: Sel, e: ReactMouseEvent<HTMLElement>) => {
    const el = e.currentTarget;
    select(next, {
      viaKeyboard: e.detail === 0,
      restore: () => {
        if (el.isConnected) el.focus({ preventScroll: true });
      },
      anchor: { el },
    });
  };

  const clear = useCallback((restoreFocus = false) => {
    setSel(null);
    if (restoreFocus) restore.current?.();
  }, []);

  const drillTo = useCallback(
    (id: string, moveFocus: boolean) => {
      // The activated element is about to unmount: land on the first skill of the new set,
      // or on the breadcrumb when the domain has no skills to land on.
      const first = M.skillsByDomain.get(id)?.[0];
      pendingFocus.current = !moveFocus ? null : first ? { kind: 'axis', id: first.id } : { kind: 'crumb' };
      setView({ domainId: id, pivotId: id });
      setSel(null);
      setHover(null);
    },
    [M],
  );

  const goUp = useCallback(() => {
    const from = view.domainId;
    if (!from) return;
    pendingFocus.current = { kind: 'axis', id: from };
    setView({ domainId: null, pivotId: from });
    setSel(null);
    setHover(null);
  }, [view.domainId]);

  // After a level change, focus the axis (or list row) the change was about.
  useEffect(() => {
    const target = pendingFocus.current;
    if (!target) return;
    pendingFocus.current = null;
    if (target.kind === 'crumb') {
      crumbRef.current?.focus({ preventScroll: true });
      return;
    }
    radarRef.current?.focusAxis(target.id);
    plotRef.current
      ?.querySelector<HTMLButtonElement>(`.now-skill-row[data-id="${CSS.escape(target.id)}"]`)
      ?.focus({ preventScroll: true });
  }, [view.domainId]);

  // Escape: close the popover (focus back to its trigger); with nothing open, go up a level.
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

  /* what lights up for the current selection */
  const related = useMemo(() => {
    const r = new Set<string>();
    if (!sel) return r;
    r.add(keyOf(sel.kind, sel.id));
    if (sel.kind === 'skill') {
      const s = M.skillById.get(sel.id);
      if (s) {
        r.add(keyOf('domain', M.homeOf(s).id));
        for (const src of M.sourcesBySkill.get(s.id) ?? []) r.add(keyOf('source', src.id));
      }
    } else if (sel.kind === 'domain') {
      r.add(keyOf('you', 'you'));
      for (const s of M.skillsByDomain.get(sel.id) ?? []) r.add(keyOf('skill', s.id));
    } else if (sel.kind === 'source') {
      for (const id of M.sourceById.get(sel.id)?.links ?? []) r.add(keyOf('skill', id));
    } else {
      for (const d of M.domains) r.add(keyOf('domain', d.id));
    }
    return r;
  }, [sel, M]);

  /** Sources evidencing the hovered skill vertex (level 1 only). */
  const hoverSources = useMemo(() => {
    const r = new Set<string>();
    if (!drilled || !hover) return r;
    for (const src of M.sourcesBySkill.get(hover) ?? []) r.add(src.id);
    return r;
  }, [drilled, hover, M]);

  /* radar axes: domains in profile order, or the drilled domain's skills in profile order */
  const axisKind: SelKind = drilled ? 'skill' : 'domain';
  const axes = useMemo<RadarAxis[]>(
    () =>
      drilled
        ? drilledSkills.map((s) => ({ id: s.id, label: s.name, value: s.score, meta: TIER_LABEL[tier(s.score)] }))
        : M.domains.map((d) => ({ id: d.id, label: d.short || d.name, value: d.score })),
    [drilled, drilledSkills, M],
  );
  const axisSetKey = drilled ? `domain:${drilled.id}` : 'domains';
  const selectedAxis = sel && sel.kind === axisKind ? sel.id : null;
  const axisRelated = useMemo(() => {
    const r = new Set<string>();
    for (const a of axes) if (a.id !== selectedAxis && related.has(keyOf(axisKind, a.id))) r.add(a.id);
    return r;
  }, [axes, axisKind, related, selectedAxis]);
  const showList = axes.length < 3;

  const onAxisSelect = (id: string, geom: VertexGeom, meta: { viaKeyboard: boolean }) => {
    if (!drilled) {
      drillTo(id, meta.viaKeyboard);
      return;
    }
    select({ kind: 'skill', id }, { viaKeyboard: meta.viaKeyboard, restore: () => radarRef.current?.focusAxis(id), anchor: { geom } });
  };

  /* popover model + placement (chart-column coordinates) */
  const pop = useMemo<NowPop | null>(() => {
    if (!sel) return null;
    const key = keyOf(sel.kind, sel.id);

    /** The chart column's box: everything is placed relative to it. */
    const frame = () => {
      const chart = chartRef.current;
      return { cb: chart?.getBoundingClientRect() ?? new DOMRect(), W: chart?.clientWidth ?? POP_W + 2 * PAD, H: chart?.clientHeight ?? 0 };
    };
    const rel = (el: Element, cb: DOMRect) => {
      const r = el.getBoundingClientRect();
      return { left: r.left - cb.left, top: r.top - cb.top, w: r.width, h: r.height };
    };
    const radarEl = () => plotRef.current?.querySelector<HTMLElement>('.radar') ?? plotRef.current;
    const clampLeft = (left: number, W: number) => Math.round(clamp(left, PAD, Math.max(PAD, W - PAD - POP_W)));

    /** Under an element (a source chip, a list row), centred on it; above it when there is no room below. */
    const under = (el: HTMLElement | null | undefined, h: number) => {
      const { cb, W, H } = frame();
      if (!el) return { left: PAD, top: PAD };
      const r = rel(el, cb);
      let top = r.top + r.h + PAD;
      if (top + h > H - PAD && r.top - PAD - h >= PAD) top = r.top - PAD - h;
      return { left: clampLeft(r.left + r.w / 2 - POP_W / 2, W), top: Math.round(top) };
    };
    /** Beside a radar vertex, on the side with more room. */
    const atVertex = (h: number) => {
      const { cb, W, H } = frame();
      const el = radarEl();
      const geom = anchor.current.geom;
      if (!geom || !el) return under(anchor.current.el, h);
      const r = rel(el, cb);
      return placePopover(r.left + geom.x, r.top + geom.y, 8, POP_W, h, { w: W, h: H }, PAD);
    };
    /** Right of the radar's centre (the "you" panel). */
    const besideCentre = (h: number) => {
      const { cb, W, H } = frame();
      const el = radarEl();
      if (!el) return { left: PAD, top: PAD };
      const r = rel(el, cb);
      const cxp = r.left + r.w / 2;
      const cyp = r.top + r.h / 2;
      return { left: clampLeft(cxp + 90, W), top: Math.round(clamp(cyp - 170, PAD + 2, Math.max(PAD + 2, H - h - PAD - 2))) };
    };
    /** The domain panel's fixed slot: right of the radar on a wide column, else below it. */
    const domainSlot = (h: number) => {
      const { cb, W, H } = frame();
      const el = plotRef.current;
      if (!el) return { left: PAD, top: PAD };
      const r = rel(el, cb);
      if (wide) return { left: Math.round(W - POP_W), top: Math.round(clamp(r.top + 24, PAD, Math.max(PAD, H - h - PAD))) };
      return { left: clampLeft((W - POP_W) / 2, W), top: Math.round(Math.max(r.top + PAD, Math.min(r.top + r.h + PAD, H - h - PAD))) };
    };

    if (sel.kind === 'skill') {
      const s = M.skillById.get(sel.id);
      if (!s) return null;
      const home = M.homeOf(s);
      const action = skillAction(s, home.name);
      return {
        model: {
          key,
          kicker: home.name,
          title: s.name,
          score: s.score,
          badge: TIER_LABEL[tier(s.score)],
          bullets: s.evidence.slice(0, 3).map((ev) => ({ tag: M.anySource.get(ev.sourceId)?.label ?? ev.sourceId, text: ev.text })),
          foot: s.unlocks.length ? { label: 'Unlocks', value: s.unlocks.join(' · ') } : undefined,
          cta: action.cta,
        },
        action,
        place: atVertex,
      };
    }
    if (sel.kind === 'domain') {
      const d = M.domainById.get(sel.id);
      if (!d) return null;
      const mine = [...(M.skillsByDomain.get(d.id) ?? [])].sort((a, b) => b.score - a.score);
      const bullets: PopoverBullet[] = [];
      if (mine.length) bullets.push({ tag: 'Top', text: `${mine[0].name} · ${mine[0].score}` });
      if (mine.length > 1) {
        const w = mine[mine.length - 1];
        bullets.push({ tag: 'Weakest', text: `${w.name} · ${w.score}` });
      }
      const action = domainAction(d);
      return {
        model: {
          key,
          kicker: `Domain · ${plural(mine.length, 'skill')}`,
          title: d.name,
          score: d.score,
          badge: TIER_LABEL[tier(d.score)],
          summary: d.summary || undefined,
          bullets,
          // The skill rows are tappable in the phone sheet only (the radar is small there).
          skills: narrow ? mine.map((s) => ({ id: s.id, name: s.name, score: s.score })) : undefined,
          cta: action.cta,
        },
        action,
        place: domainSlot,
      };
    }
    if (sel.kind === 'source') {
      const src = M.sourceById.get(sel.id);
      if (!src) return null;
      const notes = src.notes.length ? src.notes.slice(0, 3) : src.summary ? [src.summary] : [];
      const action = sourceAction(src);
      return {
        model: {
          key,
          kicker: src.sub ? `Source · ${src.sub}` : 'Source',
          title: src.label,
          bullets: notes.map((text) => ({ tag: src.label, text })),
          foot: { label: 'Evidence for', value: src.links.length ? `${plural(src.links.length, 'skill')} on your graph` : 'no skills yet' },
          cta: action.cta,
        },
        action,
        place: (h) => under(anchor.current.el, h),
      };
    }
    const ranked = [...M.skills].sort((a, b) => b.score - a.score);
    const top = ranked.slice(0, 2);
    const low = ranked
      .slice(-2)
      .reverse()
      .filter((s) => !top.includes(s));
    const bullets: PopoverBullet[] = [];
    if (top.length) bullets.push({ tag: 'Strongest', text: top.map((s) => `${s.name} ${s.score}`).join(' · ') });
    if (low.length) bullets.push({ tag: 'Holding back', text: low.map((s) => `${s.name} ${s.score}`).join(' · ') });
    const action = youAction(profile);
    return {
      model: {
        key,
        kicker: `You · ${profile.person.currentRole || 'today'}`,
        title: 'Role mastery',
        score: profile.mastery.score,
        badge: TIER_LABEL[tier(profile.mastery.score)],
        bullets,
        cta: action.cta,
      },
      action,
      place: besideCentre,
    };
    // chartSize.width is a dependency on purpose: a new `place` re-places the open popover on resize.
  }, [sel, M, profile, narrow, wide, chartSize.width, chartRef]);

  const openAct = (a: NodeAction) => actions.openChat(a.origin, a.question);

  const verb = narrow ? 'Tap' : 'Click';
  const hint = drilled
    ? `${verb} any skill · each one opens a plan${narrow ? '' : ' · Escape goes back up'}`
    : `${verb} any axis · each one opens a plan`;

  /* the centre slot: role mastery at level 0, the domain score at level 1 */
  const centre = drilled ? (
    <button
      type="button"
      className="now-centre"
      aria-pressed={isSel('domain', drilled.id)}
      aria-label={`${drilled.name}, domain score ${drilled.score}`}
      onClick={(e) => selectFrom({ kind: 'domain', id: drilled.id }, e)}
    >
      <span className="serif now-centre-num is-domain" aria-hidden="true">
        {drilled.score}
      </span>
      <span className="kicker">{drilled.short || drilled.name}</span>
    </button>
  ) : (
    <button
      type="button"
      className="now-centre"
      aria-pressed={isSel('you', 'you')}
      aria-label={`You, ${role}, role mastery ${profile.mastery.score}`}
      onClick={(e) => selectFrom({ kind: 'you', id: 'you' }, e)}
    >
      <span className="serif now-centre-num" aria-hidden="true">
        {mastery}
      </span>
      <span className="kicker">role mastery</span>
    </button>
  );

  const popover = (sheet: boolean) =>
    pop && (
      <Popover
        key={pop.model.key}
        model={pop.model}
        place={sheet ? undefined : pop.place}
        sheet={sheet}
        autoFocus={focusPop ? 'dialog' : false}
        onClose={() => clear(true)}
        onAct={() => openAct(pop.action)}
        onPickSkill={
          sheet
            ? (id, e) => {
                anchor.current = {};
                setFocusPop(e.detail === 0);
                setSel({ kind: 'skill', id });
              }
            : undefined
        }
      />
    );

  return (
    <div className="now-row" data-level={drilled ? 1 : 0}>
      {/* ─────────── rail ─────────── */}
      <aside className="now-rail" aria-label="Profile summary">
        <div className="now-rail-head">
          <span className="kicker">Profile</span>
          <h1 className="serif now-name">{profile.person.name}</h1>
          {profile.person.headline && <p className="now-headline">{profile.person.headline}</p>}
        </div>

        <button type="button" className="now-mastery" aria-pressed={isSel('you', 'you')} onClick={(e) => selectFrom({ kind: 'you', id: 'you' }, e)}>
          <span className="now-mastery-top">
            <span className="kicker">
              Role mastery<span className="sr-only">: {profile.mastery.score} out of 100.</span>
            </span>
            <span className="numeral now-mastery-num" aria-hidden="true">
              {mastery}
              <span className="now-of"> / 100</span>
            </span>
          </span>
          <ValueLine value={profile.mastery.score} />
          {profile.mastery.insight && <span className="now-mastery-insight">{profile.mastery.insight}</span>}
        </button>

        <div className="now-doms">
          <span className="kicker now-doms-k">Domains</span>
          <div className="now-dom-list">
            {M.domains.map((d) => {
              const on = drilled?.id === d.id;
              const rel = !on && related.has(keyOf('domain', d.id)) && sel?.kind !== 'you';
              return (
                <button
                  key={d.id}
                  type="button"
                  className={cx('now-dom-row', rel && 'is-rel')}
                  aria-pressed={on}
                  aria-label={`${d.name}, ${d.score} out of 100`}
                  onClick={() => (on ? goUp() : drillTo(d.id, false))}
                >
                  <span className="now-dom-name">{d.name}</span>
                  <span className="numeral now-dom-num">{d.score}</span>
                  <ValueLine value={d.score} />
                </button>
              );
            })}
          </div>
        </div>

        <p className="kicker now-legend" aria-label="Legend">
          {TIER_LABEL.expert} 80+ · {TIER_LABEL.pro} 60–79 · {TIER_LABEL.dev} 40–59 · {TIER_LABEL.gap} under 40
        </p>
      </aside>

      {/* ─────────── chart column ─────────── */}
      <section
        ref={chartRef}
        className={cx('now-chart', drilled && 'is-drilled', wide && 'is-wide')}
        aria-label="Skill radar"
        onClick={(e) => {
          // A click on the column's own space (not a control, the plot or the popover) closes the popover.
          if ((e.target as Element).closest('button, a, .pop, .radar')) return;
          clear();
        }}
      >
        <p className="sr-only" aria-live="polite">
          {drilled ? `Showing ${plural(drilledSkills.length, 'skill')} in ${drilled.name}` : 'Showing all domains'}
        </p>

        {drilled ? (
          <motion.div key={drilled.id} className="now-head now-head-domain" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.25, ease: EASE }}>
            <nav className="kicker now-crumb" aria-label="Breadcrumb">
              <button ref={crumbRef} type="button" className="now-crumb-up" onClick={goUp}>
                All domains
              </button>
              <svg className="now-crumb-sep" width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                <path d="M4.5 2.5 8 6 4.5 9.5" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span aria-current="page">{drilled.name}</span>
            </nav>
            <div className="now-dom-head">
              <div className="now-dom-id">
                <h2 className="serif now-title">{drilled.name}</h2>
                <div className="now-dom-score">
                  <span className="numeral">{drilled.score}</span>
                  <span className="now-of">/ 100</span>
                  <span className="chip">{TIER_LABEL[tier(drilled.score)]}</span>
                </div>
              </div>
              <div className="now-dom-copy">
                {drilled.summary && <p className="now-dom-summary">{drilled.summary}</p>}
                <button type="button" className="now-lnk" onClick={() => openAct(domainAction(drilled))}>
                  {domainAction(drilled).cta}
                </button>
              </div>
            </div>
          </motion.div>
        ) : (
          <motion.header key="all" className="now-head" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.25, ease: EASE }}>
            <span className="kicker">Skill radar · today</span>
            <h2 className="serif now-title">
              Where you stand as {article(role)} {role}
            </h2>
          </motion.header>
        )}

        {!drilled && <PortfolioInvite profile={profile} cinematic={cinematic} />}

        <div ref={plotRef} className="now-plot">
          {showList ? (
            <div className="panel now-skill-list" role="group" aria-label={drilled ? `Skills in ${drilled.name}` : 'Domains'}>
              {drilled && drilledSkills.length === 0 && <p className="now-skill-empty">No skills mapped to this domain yet.</p>}
              {drilled
                ? drilledSkills.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      data-id={s.id}
                      className="now-skill-row"
                      aria-pressed={isSel('skill', s.id)}
                      aria-label={`${s.name}, ${s.score} out of 100, ${TIER_LABEL[tier(s.score)]}`}
                      onClick={(e) => selectFrom({ kind: 'skill', id: s.id }, e)}
                    >
                      <span className="now-skill-name">{s.name}</span>
                      <span className="numeral now-skill-num">{s.score}</span>
                      <ValueLine value={s.score} />
                    </button>
                  ))
                : M.domains.map((d) => (
                    <button
                      key={d.id}
                      type="button"
                      data-id={d.id}
                      className="now-skill-row"
                      aria-label={`${d.name}, ${d.score} out of 100`}
                      onClick={(e) => drillTo(d.id, e.detail === 0)}
                    >
                      <span className="now-skill-name">{d.name}</span>
                      <span className="numeral now-skill-num">{d.score}</span>
                      <ValueLine value={d.score} />
                    </button>
                  ))}
            </div>
          ) : (
            <Radar
              ref={radarRef}
              className="now-radar"
              axes={axes}
              axisSetKey={axisSetKey}
              series={{ primary: 'You' }}
              selectedId={selectedAxis}
              // Level-0 axes drill down (no state to expose); level-1 axes toggle the skill popover.
              axisState={drilled ? 'pressed' : undefined}
              hoveredId={hover}
              relatedIds={axisRelated}
              dim={!!sel}
              pivotId={view.pivotId}
              reveal={cinematic}
              ariaLabel={
                drilled
                  ? `Skill radar: ${plural(drilledSkills.length, 'skill')} in ${drilled.name}`
                  : `Skill radar: ${plural(M.domains.length, 'domain')}, ${plural(M.skills.length, 'skill')}, ${plural(M.sources.length, 'source')}`
              }
              tableCaption={drilled ? `Skills in ${drilled.name}, 0 to 100` : 'Domain scores, 0 to 100'}
              centre={centre}
              onSelect={onAxisSelect}
              onHover={setHover}
              onBackgroundClick={() => clear()}
            />
          )}
        </div>

        {M.sources.length > 0 && (
          <ul className="now-sources" aria-label="Sources">
            {M.sources.map((src) => {
              const on = isSel('source', src.id);
              const lit = related.has(keyOf('source', src.id)) || hoverSources.has(src.id);
              return (
                <li key={src.id}>
                  <button
                    type="button"
                    className={cx('chip now-src', !on && lit && 'is-rel', !!sel && !on && !lit && 'is-dim')}
                    aria-pressed={on}
                    aria-label={`${src.label} source, ${src.sub}`}
                    title={`${src.label} · ${src.sub}`}
                    onClick={(e) => selectFrom({ kind: 'source', id: src.id }, e)}
                  >
                    {src.label}
                    {src.sub && <span className="now-src-sub">{src.sub}</span>}
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        <p className="kicker now-hint">{hint}</p>

        {/* popover (desktop / tablet) */}
        <AnimatePresence>{!narrow && popover(false)}</AnimatePresence>
      </section>

      {/* bottom sheet (phones) */}
      {narrow &&
        createPortal(
          <div className="now-sheet-layer">
            <AnimatePresence>{!flipped && popover(true)}</AnimatePresence>
          </div>,
          document.body,
        )}
    </div>
  );
}
