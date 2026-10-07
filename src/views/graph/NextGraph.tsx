/**
 * The back face of the graph: the most probable next roles (CalmNext board).
 *
 * Left: roles ranked by alignment, each with a glyph of its requirement radar.
 * Centre: the selected role (title, alignment, why, one action), its requirements
 * as have/gap chips, and a radar of "You" against what the role expects. A
 * requirement vertex opens the requirement popover; its one action seeds the chat.
 * Every store action, seed question and keyboard path of the previous version
 * is kept; only the visual layer changed.
 */
import { AnimatePresence } from 'motion/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { Profile, Role, RoleRequirement } from '../../../shared/types';
import { Popover } from '../../components/Popover';
import type { PopoverModel } from '../../components/Popover';
import { Radar, RadarGlyph } from '../../components/Radar';
import type { RadarAxis, RadarHandle, VertexGeom } from '../../components/Radar';
import { ValueLine } from '../../components/ValueLine';
import { TIER_LABEL, orderRequirements, placePopover, rankRoles, reqKey, tier } from '../../lib/layout';
import { requirementAction, roleAction } from '../../lib/prompts';
import { NARROW_QUERY, useMediaQuery } from '../../lib/ui';
import { actions, useApp } from '../../store';
import '../../styles/next.css';

const COUNT_WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];
const POP_W = 304;
/** Clearance between a vertex and the popover's edge: the dot plus a little air. */
const VERTEX_R = 8;
/** Edge padding when the popover hangs under a requirement row (the short-list case). */
const PAD = 8;

/** "Six paths from here" */
function pathsTitle(n: number) {
  const word = COUNT_WORDS[n] ?? String(n);
  return `${word} ${n === 1 ? 'path' : 'paths'} from here`;
}

/** Given the popover's real height, where it goes inside the plot. */
type Placer = (height: number) => { left: number; top: number };

export function NextGraph({ profile }: { profile: Profile }) {
  const narrow = useMediaQuery(NARROW_QUERY);
  const flipped = useApp((s) => s.flipped);

  const roles = useMemo(() => rankRoles(profile), [profile]);

  // Selection is remembered per profile, so a new analysis starts on its top role.
  const [picked, setPicked] = useState<{ profileId: string; roleId: string } | null>(null);
  const pickedId = picked && picked.profileId === profile.id ? picked.roleId : null;
  const sel: Role | undefined = roles.find((r) => r.id === pickedId) ?? roles[0];
  const selRank = sel ? roles.indexOf(sel) : -1;

  const [openReq, setOpenReq] = useState<{ roleId: string; key: string } | null>(null);
  const [hoverKey, setHoverKey] = useState<string | null>(null);

  // Flipping back to the current role closes the requirement popover.
  useEffect(() => {
    if (!flipped) setOpenReq(null);
  }, [flipped]);

  const reqs = useMemo(() => (sel ? orderRequirements(sel) : []), [sel]);
  const openKey = sel && openReq && openReq.roleId === sel.id ? openReq.key : null;
  const popReq = openKey ? reqs.find((r, i) => reqKey(r, i) === openKey) : undefined;
  const axes = useMemo<RadarAxis[]>(
    () => reqs.map((q, i) => ({ id: reqKey(q, i), label: q.name, value: q.current, secondary: q.target })),
    [reqs],
  );
  // Fewer than three requirements cannot make a polygon: a quiet list stands in for the radar.
  const hasRadar = reqs.length >= 3;

  /* ───────── rail list: scroll fades, and the selected row kept in view ───────── */

  const listRef = useRef<HTMLDivElement>(null);
  const [listFade, setListFade] = useState({ top: false, bottom: false });
  const measureList = () => {
    const el = listRef.current;
    if (!el) return;
    const top = el.scrollTop > 2;
    const bottom = el.scrollHeight - el.clientHeight - el.scrollTop > 2;
    setListFade((f) => (f.top === top && f.bottom === bottom ? f : { top, bottom }));
  };
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => measureList());
    ro.observe(el);
    return () => ro.disconnect();
  }, [roles.length]);
  // Keep the selected row visible inside the list (without scrolling the page).
  const selIdForScroll = sel?.id;
  useEffect(() => {
    const list = listRef.current;
    if (!list || !selIdForScroll) return;
    const row = [...list.querySelectorAll<HTMLElement>('[data-role-id]')].find((n) => n.dataset.roleId === selIdForScroll);
    if (!row) return;
    const top = row.offsetTop; // the list is position: relative, so this is list-relative
    const bottom = top + row.offsetHeight;
    if (top < list.scrollTop) list.scrollTo({ top: top - 4, behavior: 'smooth' });
    else if (bottom > list.scrollTop + list.clientHeight) list.scrollTo({ top: bottom - list.clientHeight + 4, behavior: 'smooth' });
  }, [selIdForScroll]);

  /* ───────── requirement popover: placement, focus, keys ───────── */

  const radarRef = useRef<RadarHandle>(null);
  // Rows of the short list (no radar), by requirement key, so Escape can hand focus back.
  const rowRefs = useRef(new Map<string, HTMLButtonElement>());
  const mainRef = useRef<HTMLElement>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  // Set when a requirement opens; the Popover asks it for a spot once it knows its height.
  const placerRef = useRef<Placer | null>(null);
  const place = useCallback<Placer>((h) => placerRef.current?.(h) ?? { left: PAD, top: PAD }, []);

  const focusReq = (key: string) => {
    if (hasRadar) radarRef.current?.focusAxis(key);
    else rowRefs.current.get(key)?.focus({ preventScroll: true });
  };

  const selectRole = (id: string) => {
    setPicked({ profileId: profile.id, roleId: id });
    setOpenReq(null);
  };

  /** Open a requirement's popover, or close it when that one is already open. */
  const toggleReq = (key: string, placer: Placer) => {
    if (!sel) return;
    if (key === openKey) {
      setOpenReq(null);
      return;
    }
    placerRef.current = placer;
    setOpenReq({ roleId: sel.id, key });
  };

  // A radar vertex: beside the point, on the side with more room. The plot is only 560px wide, so a
  // card kept inside it would sit on the vertices near the vertical axis; sideways it may use the
  // whole centre column (the plot is centred in it), while its height still keeps it over the plot.
  // A label centred over its vertex (the top and bottom axes) cannot be cleared sideways, so the
  // card then starts just under it (or ends just above it) instead of rising into it.
  const onVertex = (key: string, geom: VertexGeom) =>
    toggleReq(key, (h) => {
      const main = mainRef.current;
      const plot = plotRef.current;
      const radar = plot?.querySelector<HTMLElement>('.radar');
      if (!main || !plot || !radar) return placePopover(geom.x, geom.y, VERTEX_R, POP_W, h, geom.bounds);
      const mainLeft = main.getBoundingClientRect().left;
      const plotLeft = plot.getBoundingClientRect().left - mainLeft;
      const radarLeft = radar.getBoundingClientRect().left - mainLeft;
      const p = placePopover(radarLeft + geom.x, geom.y, VERTEX_R, POP_W, h, { w: main.clientWidth, h: geom.bounds.h });
      let { top } = p;
      const L = geom.label;
      const cardLeft = p.left - radarLeft; // in the radar's coordinates, like the label
      if (L && cardLeft < L.x + L.w && cardLeft + POP_W > L.x && top < L.y + L.h && top + h > L.y) {
        const labelAbove = L.y + L.h / 2 < geom.y;
        top = labelAbove ? L.y + L.h + PAD : L.y - PAD - h;
        top = Math.round(Math.max(PAD + 2, Math.min(geom.bounds.h - h - PAD - 2, top)));
      }
      return { left: p.left - plotLeft, top }; // back to the plot, where the card is positioned
    });

  // A list row: under the row, kept inside the plot's width.
  const onRow = (key: string, el: HTMLButtonElement) =>
    toggleReq(key, () => {
      const w = plotRef.current?.clientWidth ?? el.offsetWidth;
      return { left: Math.max(PAD, Math.min(w - PAD - POP_W, el.offsetLeft)), top: el.offsetTop + el.offsetHeight + PAD };
    });

  const closePop = (restoreFocus: boolean) => {
    const key = openKey;
    setOpenReq(null);
    if (restoreFocus && key) focusReq(key);
  };

  // Escape closes the requirement popover and returns focus to its vertex. An Escape the Atmosphere
  // menu or the New-analysis dialog already took (defaultPrevented) is theirs: one key, one layer.
  useEffect(() => {
    if (!openKey) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      e.preventDefault();
      setOpenReq(null);
      focusReq(openKey);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [openKey]);

  /* ───────── copy ───────── */

  // The single largest gap gets the "moves your alignment most" copy.
  const biggestGap = useMemo(() => {
    let best: RoleRequirement | undefined;
    for (const r of reqs) if (!r.have && (!best || r.target - r.current > best.target - best.current)) best = r;
    return best;
  }, [reqs]);
  const gapCount = reqs.filter((r) => !r.have).length;
  const hasGithub = profile.sources.some((s) => s.kind === 'github' && s.ok);

  const haveChips = sel ? sel.requirements.filter((r) => r.have) : [];
  const gapChips = sel ? sel.requirements.filter((r) => !r.have) : [];

  const openRoleChat = () => {
    if (!sel) return;
    const a = roleAction(sel);
    actions.openChat(a.origin, a.question);
  };

  let pop: { model: PopoverModel; run: () => void } | null = null;
  if (sel && popReq && openKey) {
    const a = requirementAction(popReq, sel);
    const body = popReq.have
      ? `You are at ${popReq.current}. This already counts toward ${sel.title} — make it visible in your ${hasGithub ? 'pinned repos' : 'projects'} and resume.`
      : `You are at ${popReq.current}; the role expects about ${popReq.target}. ` +
        (popReq === biggestGap
          ? 'Closing this moves your alignment more than any other single skill.'
          : `A ${Math.max(0, popReq.target - popReq.current)}-point gap — one of ${gapCount} between you and this role.`);
    pop = {
      model: {
        key: `${sel.id}:${openKey}`,
        kicker: `${sel.title} · ${popReq.have ? 'strength' : 'gap'}`,
        title: popReq.name,
        score: popReq.current,
        suffix: popReq.have ? '/ 100' : `→ ${popReq.target} needed`,
        badge: popReq.have ? TIER_LABEL[tier(popReq.current)] : `+${Math.max(0, popReq.target - popReq.current)} to close`,
        ...(popReq.have ? {} : { target: popReq.target }),
        summary: body,
        cta: a.cta,
      },
      run: () => actions.openChat(a.origin, a.question),
    };
  }

  /* ───────── render ───────── */

  return (
    <div className="next-row">
      {/* ───────── rail ───────── */}
      <aside className="next-rail" aria-label="Next roles">
        <div className="next-rail-head">
          <span className="kicker">Next roles</span>
          <h2 className="serif next-title">{roles.length ? pathsTitle(roles.length) : 'No paths yet'}</h2>
          <p className="next-sub">
            {roles.length
              ? 'Alignment is how much of the role you already cover.'
              : 'Add another source — a GitHub profile or a project link — and Tayyari will map where you could go next.'}
          </p>
        </div>

        {roles.length > 0 && (
          <div
            ref={listRef}
            className={`next-roles scroll${listFade.top ? ' fade-top' : ''}${listFade.bottom ? ' fade-bottom' : ''}`}
            role="group"
            aria-label="Roles ranked by alignment"
            onScroll={measureList}
          >
            {roles.map((r, i) => {
              const on = r.id === sel?.id;
              return (
                <button
                  key={r.id}
                  data-role-id={r.id}
                  type="button"
                  className="next-role-row"
                  aria-pressed={on}
                  aria-label={`${i + 1}. ${r.title}, ${r.alignment} percent aligned, ${r.eta}`}
                  onClick={() => selectRole(r.id)}
                >
                  <span className="numeral next-rank" aria-hidden="true">
                    {i + 1}
                  </span>
                  <span className="next-role-title">{r.title}</span>
                  <RadarGlyph className="next-role-glyph" values={orderRequirements(r).map((q) => q.current)} />
                  <span className="next-role-meta">
                    <span className="numeral next-role-pct">{r.alignment}%</span>
                    <span className="kicker next-role-eta">{r.eta}</span>
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </aside>

      {/* ───────── centre ───────── */}
      <section ref={mainRef} className="next-main">
        {sel ? (
          <>
            <header className="next-head">
              <div className="next-head-id">
                <span className="kicker">{selRank === 0 ? 'Most probable next role' : 'Next role'}</span>
                <h2 className="serif next-role-name">{sel.title}</h2>
                <p className="next-align">
                  <span className="numeral">{sel.alignment}% aligned</span> <span className="next-muted">· {sel.eta}</span>
                </p>
              </div>
              <div className="next-head-copy">
                {sel.why && <p className="next-why">{sel.why}</p>}
                <div>
                  <button type="button" className="btn btn-primary next-cta" onClick={openRoleChat}>
                    {roleAction(sel).cta}
                  </button>
                </div>
              </div>
            </header>

            {haveChips.length + gapChips.length > 0 ? (
              <ul className="next-chips" aria-label="What this role asks for">
                {haveChips.map((h, i) => (
                  <li key={`h${i}`} className="chip next-chip">
                    <span className="sr-only">You have </span>
                    {i === 0 && (
                      <span className="next-chip-group" aria-hidden="true">
                        You have ·
                      </span>
                    )}
                    {h.name}
                  </li>
                ))}
                {gapChips.map((g, i) => (
                  <li key={`g${i}`} className="chip next-chip is-gap">
                    <span className="sr-only">Gap: </span>
                    {i === 0 && (
                      <span className="next-chip-group" aria-hidden="true">
                        Gap ·
                      </span>
                    )}
                    {g.name}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="next-why">No specific requirements listed yet — ask and Tayyari will break the role down for you.</p>
            )}

            {reqs.length > 0 && (
              <div ref={plotRef} className="next-plot">
                {hasRadar ? (
                  <>
                    <Radar
                      ref={radarRef}
                      axes={axes}
                      axisSetKey={`role:${sel.id}`}
                      series={{ primary: 'You', secondary: 'Role expects' }}
                      selectedId={openKey}
                      axisState="expanded"
                      hoveredId={hoverKey}
                      ariaLabel={`${sel.title} requirements radar: you versus what the role expects`}
                      tableCaption={`${sel.title}: your level today against what the role expects`}
                      onSelect={(id, geom) => onVertex(id, geom)}
                      onHover={setHoverKey}
                      onBackgroundClick={() => setOpenReq(null)}
                    />
                    <p className="next-legend" aria-hidden="true">
                      <span>
                        <svg width="24" height="8" viewBox="0 0 24 8" focusable="false">
                          <line className="next-lg-you" x1="0" y1="4" x2="24" y2="4" />
                        </svg>
                        You
                      </span>
                      <span>
                        <svg width="24" height="8" viewBox="0 0 24 8" focusable="false">
                          <line className="next-lg-exp" x1="0" y1="4" x2="24" y2="4" />
                        </svg>
                        Role expects
                      </span>
                    </p>
                  </>
                ) : (
                  <div className="panel next-req-list" role="group" aria-label={`${sel.title} requirements`}>
                    {reqs.map((q, i) => {
                      const k = reqKey(q, i);
                      const on = k === openKey;
                      return (
                        <button
                          key={k}
                          ref={(el) => {
                            if (el) rowRefs.current.set(k, el);
                            else rowRefs.current.delete(k);
                          }}
                          type="button"
                          className="next-req-row"
                          aria-haspopup="dialog"
                          aria-expanded={on}
                          aria-label={`${q.name}, ${q.have ? `you have it at ${q.current}` : `gap, ${q.current} now, ${q.target} needed`}`}
                          onClick={(e) => onRow(k, e.currentTarget)}
                        >
                          <span className="next-req-name">{q.name}</span>
                          <span className="numeral next-req-val">
                            {q.current} / {q.target}
                          </span>
                          <ValueLine value={q.current} target={q.target} />
                        </button>
                      );
                    })}
                  </div>
                )}

                {/* The requirement popover, anchored inside the plot (desktop and tablet). */}
                <AnimatePresence>
                  {pop && !narrow && (
                    <Popover
                      key={pop.model.key}
                      model={pop.model}
                      place={place}
                      width={POP_W}
                      autoFocus="cta"
                      onClose={() => closePop(true)}
                      onAct={pop.run}
                    />
                  )}
                </AnimatePresence>
              </div>
            )}
          </>
        ) : (
          <p className="next-empty">Next roles appear here once Tayyari has enough evidence.</p>
        )}
      </section>

      {/* Phones: the same popover as a bottom sheet over the page, outside the face's crossfade. */}
      {narrow &&
        createPortal(
          <AnimatePresence>
            {pop && flipped && (
              <Popover key={pop.model.key} model={pop.model} sheet autoFocus="cta" onClose={() => closePop(true)} onAct={pop.run} />
            )}
          </AnimatePresence>,
          document.body,
        )}
    </div>
  );
}
