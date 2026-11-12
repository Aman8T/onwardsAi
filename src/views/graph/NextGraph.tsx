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
import type { PreparePlanSummary } from '../../../shared/prepare';
import { PageLine } from '../../components/materials';
import { dayLabel } from '../../discover/prepare/words';
import { useLoad } from '../../practice/hub/useLoad';
import { listPlans } from '../../prepare/api';
import { gapSpots } from './gapLayout';
import { Popover } from '../../components/Popover';
import type { PopoverModel } from '../../components/Popover';
import { Radar, RadarGlyph } from '../../components/Radar';
import type { RadarAxis, RadarHandle, VertexGeom } from '../../components/Radar';
import { ValueLine } from '../../components/ValueLine';
import { TIER_LABEL, orderRequirements, placePopover, rankRoles, reqKey, roleStanding, tier } from '../../lib/layout';
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

export function NextGraph({ profile, initialRoleId = null }: { profile: Profile; initialRoleId?: string | null }) {
  const narrow = useMediaQuery(NARROW_QUERY);
  const flipped = useApp((s) => s.flipped);

  const roles = useMemo(() => rankRoles(profile), [profile]);

  // Selection is remembered per profile, so a new analysis starts on its top role; the dashboard may name the role to open on.
  const [picked, setPicked] = useState<{ profileId: string; roleId: string } | null>(() =>
    initialRoleId ? { profileId: profile.id, roleId: initialRoleId } : null,
  );
  useEffect(() => {
    if (initialRoleId) setPicked({ profileId: profile.id, roleId: initialRoleId });
  }, [initialRoleId, profile.id]);
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

  // "How do I prepare for this role?" opens a Prepare thread (blocks, not prose) for the role.
  const openRoleChat = () => {
    if (!sel) return;
    const a = roleAction(sel, roleStanding(profile, sel.id));
    actions.openChat(a.origin, a.question, { roleId: sel.id });
  };

  let pop: { model: PopoverModel; run: () => void } | null = null;
  if (sel && popReq && openKey) {
    const a = requirementAction(popReq, sel);
    // a gap opens a Prepare thread for just that requirement; a strength stays a chat question
    const prepare = popReq.have ? undefined : { roleId: sel.id, requirement: popReq.name };
    const body = popReq.have
      ? `You are at ${popReq.current}. This already counts toward ${sel.title}. Make it visible in your ${hasGithub ? 'pinned repos' : 'projects'} and resume.`
      : `You are at ${popReq.current}; the role expects about ${popReq.target}. ` +
        (popReq === biggestGap
          ? 'Closing this moves your alignment more than any other single skill.'
          : `A ${Math.max(0, popReq.target - popReq.current)}-point gap, one of ${gapCount} between you and this role.`);
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
      run: () => actions.openChat(a.origin, a.question, prepare),
    };
  }

  /* ───────── what the tiles rest on ───────── */

  // The two widest gaps float beside the chart; a gap shows only where the radar has an axis for it.
  const floaters = useMemo(() => {
    const open = reqs
      .map((q, i) => ({ q, key: reqKey(q, i), gap: q.target - q.current }))
      .filter((x) => !x.q.have && x.gap > 0)
      .sort((a, b) => b.gap - a.gap)
      .slice(0, 2);
    const spots = hasRadar ? gapSpots(axes.map((a) => ({ id: a.id, value: a.value })), open.map((x) => x.key)) : [];
    return open.flatMap((x) => {
      const spot = spots.find((s) => s.id === x.key);
      return spot ? [{ ...x, spot }] : [];
    });
  }, [reqs, axes, hasRadar]);

  const sourceLabel = (id: string) => profile.sources.find((x) => x.id === id)?.label ?? id;
  const gapSkill = biggestGap?.skillId ? profile.skills.find((s) => s.id === biggestGap.skillId) : undefined;
  const gapEvidence = gapSkill?.evidence ?? [];
  // what the person can already cite for this role: the evidence behind the requirements they hold, strongest first
  const cited = useMemo(
    () =>
      reqs
        .filter((q) => q.have)
        .sort((a, b) => b.current - a.current)
        .flatMap((q) => {
          const sk = q.skillId ? profile.skills.find((s) => s.id === q.skillId) : undefined;
          const ev = sk?.evidence[0];
          return ev ? [{ name: q.name, current: q.current, text: ev.text, source: profile.sources.find((x) => x.id === ev.sourceId)?.label ?? ev.sourceId }] : [];
        })
        .slice(0, 3),
    [reqs, profile],
  );

  // the stored plans for this role, from the same list the dashboard reads
  const { load: planLoad } = useLoad<PreparePlanSummary[]>((signal) => listPlans(profile.id, signal), [profile.id]);
  const plans = sel && planLoad.status === 'ready' ? planLoad.data.filter((p) => p.roleId === sel.id) : [];
  const openPlan = (p: PreparePlanSummary) => {
    if (!sel) return;
    const req = p.requirement ? sel.requirements.find((r) => r.name === p.requirement) : undefined;
    const a = req ? requirementAction(req, sel) : roleAction(sel, roleStanding(profile, sel.id));
    actions.openChat(a.origin, a.question, { roleId: sel.id, ...(req ? { requirement: req.name } : {}) });
  };

  /* ───────── render ───────── */

  const cta = sel ? roleAction(sel, roleStanding(profile, sel.id)).cta : '';

  return (
    <div className="m-screen nx-screen">
      {/* ───────── the hero: the role, its alignment, and the radar of you against it ───────── */}
      <section ref={mainRef} className="m-hero nx-hero" aria-label={sel ? `${sel.title}: you against what the role expects` : 'Next roles'}>
        {sel ? (
          <>
            <span className="kicker">{selRank === 0 ? 'Most probable next role' : 'Next role'}</span>
            <h2 className="title nx-name">{sel.title}</h2>
            <p className="m-count nx-align">
              <span className="m-big numeral">{sel.alignment}%</span>
              <span>
                aligned
                <br />
                <span className="next-muted">· {sel.eta}</span>
              </span>
            </p>

            {reqs.length > 0 && (
              <div ref={plotRef} className="next-plot nx-plot">
                {hasRadar ? (
                  <>
                    <div className="nx-box">
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
                      <div className="nx-co-layer" aria-hidden="true">
                        {floaters.map((f) => (
                          <span key={f.key}>
                            <span className="nx-ring" style={{ left: `${f.spot.px}%`, top: `${f.spot.py}%` }} />
                            <span className={`m-co nx-co is-${f.spot.align}`} style={{ left: `${f.spot.left}%`, top: `${f.spot.top}%` }}>
                              <span className="m-co-n">{f.q.current}</span>
                              <span className="m-co-l">
                                {f.q.name} · role asks {f.q.target}
                              </span>
                            </span>
                          </span>
                        ))}
                      </div>
                    </div>
                    <p className="next-legend" aria-hidden="true">
                      <span>
                        <svg width="22" height="14" viewBox="0 0 22 14" focusable="false">
                          <rect className="next-lg-you" x="1" y="1" width="20" height="12" />
                        </svg>
                        You
                      </span>
                      <span>
                        <svg width="22" height="14" viewBox="0 0 22 14" focusable="false">
                          <rect className="next-lg-exp" x="1" y="1" width="20" height="12" />
                        </svg>
                        Role expects
                      </span>
                    </p>
                  </>
                ) : (
                  <div className="nx-short" role="group" aria-label={`${sel.title} requirements`}>
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
          <p className="next-empty pen">Next roles appear here once Leafout has enough evidence.</p>
        )}
      </section>

      {/* ───────── the mosaic ───────── */}
      <div className="m-mosaic nx-mosaic">
        {sel && (
          <>
            <div className="m-tile m-quiet m-c4 nx-why">
              <span className="kicker">Why this role</span>
              {sel.why ? <p className="nx-why-t">{sel.why}</p> : <p className="m-fine">No specific reason listed yet. Ask, and Leafout will explain it.</p>}
            </div>

            <button type="button" className="m-tile m-ink m-c2 nx-cta" onClick={openRoleChat}>
              <span className="kicker">Next step</span>
              <span className="m-strong nx-cta-t">{cta}</span>
              <span className="m-foot">
                <span className="m-fine">{sel.title}</span>
                <span aria-hidden="true">→</span>
              </span>
            </button>

            {biggestGap &&
              (gapEvidence.length > 0 ? (
                <section className="m-tile m-graph m-c3" aria-label="Widest gap">
                  <span className="kicker">Widest gap</span>
                  <span className="m-strong">{biggestGap.name}</span>
                  <span className="m-cite">
                    you {biggestGap.current} · role asks {biggestGap.target}
                  </span>
                  <ul className="nx-ev">
                    {gapEvidence.slice(0, 2).map((ev, i) => (
                      <li key={i}>
                        <span className="nx-ev-t">{ev.text}</span>
                        <span className="m-cite">{sourceLabel(ev.sourceId)}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : (
                <section className="m-tile m-gap m-c3" aria-label="Widest gap">
                  <span className="kicker">Widest gap</span>
                  <span className="m-strong">{biggestGap.name}</span>
                  <span className="m-fine">nothing cited yet</span>
                  <span className="m-foot">
                    <span className="m-cite">
                      you {biggestGap.current} · role asks {biggestGap.target}
                    </span>
                  </span>
                </section>
              ))}

            <section className={`m-tile m-graph ${biggestGap ? 'm-c3' : 'm-c6'}`} aria-label="What you can already cite">
              <span className="kicker">What you can already cite</span>
              {cited.length > 0 ? (
                <ul className="nx-ev">
                  {cited.map((c) => (
                    <li key={c.name}>
                      <span className="nx-ev-t">
                        <b className="numeral">{c.current}</b> {c.name}
                      </span>
                      <span className="m-cite">{c.source}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="m-fine">No evidence is tied to this role&rsquo;s requirements yet.</p>
              )}
            </section>

            {haveChips.length + gapChips.length > 0 && (
              <section className="m-tile m-graph m-c6" aria-label="What this role asks for">
                <span className="kicker">What this role asks for</span>
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
              </section>
            )}

            {plans.length > 0 && (
              <section className="m-tile m-paper m-c6" aria-label="Your plan for this role">
                <div className="m-page-head">
                  <span className="pen nx-page-t">Your plan</span>
                  <span className="m-fine">
                    {plans.length === 1 ? 'one plan' : `${plans.length} plans`} for {sel.title}
                  </span>
                </div>
                <ul className="m-lines">
                  {plans.map((p, i) => (
                    <PageLine
                      key={p.id}
                      tick={false}
                      index={i}
                      title={p.requirement ? `Close ${p.requirement}` : `Get ready for ${p.roleTitle}`}
                      cite={`plan made ${dayLabel(p.createdAt.slice(0, 10))}`}
                      onGo={() => openPlan(p)}
                    />
                  ))}
                </ul>
              </section>
            )}
          </>
        )}

        {/* ───────── the roles, ranked: measured rows on graph paper, the closest one larger ───────── */}
        <aside className="m-tile m-graph m-c6 nx-roles" aria-label="Next roles">
          <span className="kicker">Next roles</span>
          <h3 className="serif nx-roles-t">{roles.length ? pathsTitle(roles.length) : 'No paths yet'}</h3>
          <p className="m-fine">
            {roles.length
              ? 'Alignment is how much of the role you already cover.'
              : 'Add another source, a GitHub profile or a project link, and Leafout will map where you could go next.'}
          </p>
          {roles.length > 0 && (
            <div className="next-roles" role="group" aria-label="Roles ranked by alignment">
              {roles.map((r, i) => {
                const on = r.id === sel?.id;
                return (
                  <button
                    key={r.id}
                    data-role-id={r.id}
                    type="button"
                    className={`next-role-row${i === 0 ? ' is-top' : ''}`}
                    aria-pressed={on}
                    aria-label={`${i + 1}. ${r.title}, ${r.alignment} percent aligned, ${r.eta}`}
                    onClick={() => selectRole(r.id)}
                  >
                    <span className="numeral next-rank" aria-hidden="true">
                      {i + 1}
                    </span>
                    <span className="next-role-title">{r.title}</span>
                    <RadarGlyph className="next-role-glyph" size={i === 0 ? 32 : 20} values={orderRequirements(r).map((q) => q.current)} />
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
      </div>

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
