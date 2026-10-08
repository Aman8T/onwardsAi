/**
 * Hero: name, one-line tagline (the `tagline` slot), a few facts and the two calls to action.
 * Variants: statement (huge name), split (name beside the graph; PortfolioView places the graph),
 * centered (monogram ring, everything centred), index (small name, the tagline as the headline).
 */
import { factsOf } from '../model';
import { initials } from '../../lib/ui';
import { BlockFrame, Slot, anchorId, cityOf, cx, fmtYears, usePf } from './SectionShell';
import type { BlockProps } from './SectionShell';

function useHeroFacts() {
  const { content, spec } = usePf();
  const { person } = content;
  const experience = factsOf(content, 'experience');
  const current = experience.find((f) => f.current);
  const years = person.yearsExperience && person.yearsExperience > 0 ? person.yearsExperience : undefined;
  const role = current ? (current.org ? `${current.title} at ${current.org}` : current.title) : undefined;

  const items: string[] = [];
  if (years !== undefined) items.push(`${fmtYears(years)} ${years === 1 ? 'year' : 'years'} of experience`);
  if (role) items.push(role);
  if (!experience.length) {
    const edu = factsOf(content, 'education')[0];
    if (edu) {
      items.push(edu.title);
      if (edu.org) items.push(edu.org);
      if (edu.grade && spec.privacy.grades) items.push(edu.grade);
    }
  }

  const rows: { k: string; v: string }[] = [];
  if (current) rows.push({ k: 'Now', v: current.org ? `${current.title} · ${current.org}` : current.title });
  if (years !== undefined) rows.push({ k: 'Experience', v: `${fmtYears(years)} ${years === 1 ? 'year' : 'years'}` });
  if (person.location) rows.push({ k: 'Based in', v: person.location });

  return { items: items.slice(0, 3), rows };
}

function Eyebrow() {
  const { content } = usePf();
  const { role, location, headline } = content.person;
  const text = [role, cityOf(location)].filter(Boolean).join(' · ') || headline;
  return text ? <p className="pf-eyebrow">{text}</p> : null;
}

function Name({ id }: { id: string }) {
  const { content } = usePf();
  const name = content.person.name;
  const length = Array.from(name).length;
  const size = length > 26 ? 'xl' : length > 15 ? 'long' : 'std';
  return (
    <h1 id={id} className={cx('pf-name', `pf-name--${size}`)}>
      {name}
    </h1>
  );
}

function Facts() {
  const { items } = useHeroFacts();
  if (!items.length) return null;
  return (
    <ul className="pf-hero-facts">
      {items.map((t) => (
        <li key={t}>{t}</li>
      ))}
    </ul>
  );
}

function Actions({ centered }: { centered?: boolean }) {
  const { rendered, uid } = usePf();
  const contact = rendered.contact;
  const secondary = (
    [
      ['projects', 'Selected work'],
      ['clips', 'Read the clips'],
      ['experience', 'Experience'],
    ] as const
  ).find(([kind]) => rendered[kind]);
  if (!contact && !secondary) return null;
  return (
    <div className={cx('pf-actions', centered && 'pf-actions--center')}>
      {contact ? (
        <a className="pf-btn" href={`#${anchorId(uid, contact)}`}>
          Get in touch
        </a>
      ) : null}
      {secondary ? (
        <a className="pf-btn pf-btn--ghost" href={`#${anchorId(uid, rendered[secondary[0]] as string)}`}>
          {secondary[1]}
        </a>
      ) : null}
    </div>
  );
}

export function Hero({ block, index }: BlockProps) {
  const { content, spec, uid } = usePf();
  const { rows } = useHeroFacts();
  const nameId = `${uid}-name`;
  const mono = content.person.monogram || initials(content.person.name);
  const fallback = content.person.headline;

  let body;
  if (block.variant === 'centered') {
    body = (
      <div className="pf-hero-centered">
        <div className="pf-ring" aria-hidden="true">
          <div className="pf-ring-in">{mono}</div>
        </div>
        <Eyebrow />
        <Name id={nameId} />
        <Slot id="tagline" fallback={fallback} className="pf-tagline" />
        <Facts />
        <Actions centered />
      </div>
    );
  } else if (block.variant === 'index') {
    body = (
      <div className="pf-hero-index">
        <div className="pf-index-id">
          <span className="pf-mark" aria-hidden="true">
            {mono}
          </span>
          <Name id={nameId} />
        </div>
        <Slot id="tagline" fallback={fallback} className="pf-tagline pf-tagline--index" />
        <Eyebrow />
        <Facts />
        <Actions />
      </div>
    );
  } else {
    const ledger = spec.composition === 'ledger' && rows.length > 0;
    body = (
      <div className="pf-hero-main">
        <Eyebrow />
        <Name id={nameId} />
        {ledger ? (
          <div className="pf-hero-row">
            <Slot id="tagline" fallback={fallback} className="pf-tagline" />
            <dl className="pf-hero-dl">
              {rows.map((r) => (
                <div key={r.k}>
                  <dt>{r.k}</dt>
                  <dd>{r.v}</dd>
                </div>
              ))}
            </dl>
          </div>
        ) : (
          <>
            <Slot id="tagline" fallback={fallback} className="pf-tagline" />
            <Facts />
          </>
        )}
        <Actions />
      </div>
    );
  }

  return (
    <BlockFrame block={block} index={index} labelledBy={nameId} className="pf-hero">
      {body}
    </BlockFrame>
  );
}
