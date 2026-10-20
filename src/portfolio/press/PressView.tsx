/**
 * The Old Newspaper skin: the same spec, content and graph as the standard page, set as a front page.
 *
 * Two layouts from one page model (derive.ts): a dense front page (lead story and Career / In Brief in the
 * main column, the Skills Index and boxes in the right rail) and, when there are only a couple of stories, a
 * banner front page with bigger type and fewer, wider columns. Phones get one column. The Skills Index
 * itself is drawn by the graph unit and placed through `renderIndex`.
 */
import { useEffect, useId, useMemo } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import type { PressIndexArgs, PressRenderArgs } from '../types';
import { graphVariant, needsFallbackFaces, shownDomains } from '../model';
import { buildPress } from './derive';
import type { PressAd, PressHonour, PressNumber, PressPage, PressSchool, PressStory } from './derive';
import { ensurePressFonts } from './fonts';
import '../../styles/press.css';

type Rise = () => CSSProperties;

export function PressView(props: PressRenderArgs & { renderIndex: (a: PressIndexArgs) => ReactNode }) {
  const { spec, content, graph, mode, onToggleSkin, renderIndex } = props;
  const uid = useId();
  const page = useMemo(() => buildPress(spec, content, graph, mode), [spec, content, graph, mode]);
  useEffect(() => ensurePressFonts(), []);

  // Entrance order: every block fades in 400ms, 60ms after the one before it (CSS handles reduced motion).
  let step = 0;
  const rise: Rise = () => ({ '--i': Math.min(step++, 8) }) as CSSProperties;

  const name = content.person.name;
  const domains = shownDomains(graph, spec);
  const index =
    page.showIndex && domains.length > 0 ? renderIndex({ domains, variant: graphVariant(spec), graph }) : null;
  const ids = (key: string) => `${uid}-${key}`;

  const hasLeadText = page.lead.paragraphs.length > 0;
  const stories = page.career.length > 0 || page.brief.length > 0;
  const stack = [page.career, page.brief].filter((s) => s.length > 0).length;

  const body = hasLeadText ? (
    <div className={`pv-body pv-rise${page.sparse ? ' pv-body--big' : ''}`} style={rise()}>
      {page.lead.paragraphs.map((p, i) => (
        <p key={i}>
          {i === 0 && page.lead.dateline && <span className="pv-dl">{page.lead.dateline} — </span>}
          {p}
        </p>
      ))}
    </div>
  ) : null;

  const storySections = stories ? (
    <div className={`pv-cb pv-rise${page.sparse ? ' pv-cb--big' : ''}`} style={rise()}>
      {page.career.length > 0 && (
        <StorySection id={ids('career')} title="Career" stories={page.career} className="pv-career" />
      )}
      {page.brief.length > 0 && (
        <StorySection
          id={ids('brief')}
          title="In Brief"
          stories={page.brief}
          className={`pv-brief${stack === 2 ? ' pv-brief--ruled' : ''}`}
        />
      )}
    </div>
  ) : null;

  const quote = page.quote ? (
    <figure className={`pv-quote pv-rise${page.sparse ? ' pv-quote--big' : ''}`} style={rise()}>
      <blockquote>
        <p>“{page.quote.text}”</p>
      </blockquote>
      <figcaption>{page.quote.caption}</figcaption>
    </figure>
  ) : null;

  const numbers =
    page.numbers.length > 0 ? (
      <Numbers id={ids('numbers')} numbers={page.numbers} className="pv-numbers pv-rise" style={rise()} />
    ) : null;
  const honours =
    page.honours.length > 0 ? (
      <Honours id={ids('honours')} honours={page.honours} className="pv-honours pv-rise" style={rise()} />
    ) : null;
  const school =
    page.school.length > 0 ? (
      <School id={ids('school')} school={page.school} className="pv-school pv-rise" style={rise()} />
    ) : null;

  return (
    <div className={`pv${page.sparse ? ' pv--sparse' : ''}`}>
      <div className="pv-page">
        {onToggleSkin && (
          <div className="pv-strip">
            <button type="button" className="pv-back" onClick={onToggleSkin}>
              <span aria-hidden="true">←&nbsp;</span>Back to the modern view
            </button>
          </div>
        )}

        <header className="pv-rise" style={rise()}>
          <Rules thickFirst />
          <div className="pv-plate">
            {page.earLeft ? (
              <p className="pv-ear pv-ear-l">{page.earLeft}</p>
            ) : page.earRight ? (
              <p className="pv-ear pv-ear-l pv-ear-ghost" aria-hidden="true">
                &nbsp;
              </p>
            ) : null}
            <h1 className={`pv-name ${needsFallbackFaces(name) ? 'pv-name--plain' : 'pv-name--black'}`}>{name}</h1>
            {page.earRight ? (
              <p className="pv-ear pv-ear-r">{page.earRight}</p>
            ) : page.earLeft ? (
              <p className="pv-ear pv-ear-r pv-ear-ghost" aria-hidden="true">
                &nbsp;
              </p>
            ) : null}
          </div>
          <Rules thickFirst={false} />
          <p className="pv-folio">{page.folio}</p>
        </header>

        <main>
          {page.sparse ? (
            <SparseFront
              page={page}
              body={body}
              index={index}
              stories={storySections}
              quote={quote}
              numbers={numbers}
              honours={honours}
              school={school}
              headId={ids('hl')}
              rise={rise}
            />
          ) : (
            <div className={`pv-cols${index || quote || numbers || honours || school ? '' : ' pv-cols--solo'}`}>
              <div className="pv-main">
                <Lead page={page} id={ids('hl')} className="pv-head pv-rise" style={rise()} />
                {body}
                {storySections}
              </div>
              {(index || quote || numbers || honours || school) && (
                <div className="pv-rail">
                  {index && (
                    <div className="pv-index-slot pv-rise" style={rise()}>
                      {index}
                    </div>
                  )}
                  {quote}
                  {numbers}
                  {honours}
                  {school}
                </div>
              )}
            </div>
          )}

          {page.wanted && (
            <section className="pv-corr pv-rise" style={rise()} aria-labelledby={ids('wanted')}>
              <h2 id={ids('wanted')} className="pv-sec">
                Situation Wanted
              </h2>
              <ul className="pv-ads pv-ads--one">
                <li>
                  <p className="pv-ad pv-ad--plain">
                    <span className="pv-ad-k">OPEN TO</span>
                    <span aria-hidden="true">·</span>
                    <span className="pv-ad-v">{page.wanted}</span>
                  </p>
                </li>
              </ul>
            </section>
          )}

          {page.ads.length > 0 && (
            <section className="pv-corr pv-rise" style={rise()} aria-labelledby={ids('corr')}>
              <h2 id={ids('corr')} className="pv-sec">
                Correspondence
              </h2>
              <ul className={`pv-ads${page.ads.length === 1 ? ' pv-ads--one' : ''}`}>
                {page.ads.map((ad) => (
                  <li key={ad.id}>
                    <Ad ad={ad} />
                  </li>
                ))}
              </ul>
            </section>
          )}
        </main>

        <footer className="pv-foot pv-rise" style={rise()}>
          <span>{page.footer}</span>
          <span>Made with Leafout</span>
        </footer>
      </div>
    </div>
  );
}

/* ───────────────────────── layouts ───────────────────────── */

/** Few stories: a banner headline across the page, the story and the Skills Watch side by side, then wide boxes. */
function SparseFront(props: {
  page: PressPage;
  body: ReactNode;
  index: ReactNode;
  stories: ReactNode;
  quote: ReactNode;
  numbers: ReactNode;
  honours: ReactNode;
  school: ReactNode;
  headId: string;
  rise: Rise;
}) {
  const { page, body, index, stories, quote, numbers, honours, school, headId, rise } = props;
  const lead = page.lead;
  return (
    <>
      <section className="pv-s-lead pv-rise" style={rise()} aria-labelledby={headId}>
        {lead.kicker && <p className="pv-kick">{lead.kicker}</p>}
        <h2 id={headId} className="pv-s-hl">
          {lead.headline}
        </h2>
        {lead.deck && <p className="pv-s-deck">{lead.deck}</p>}
        <p className="pv-s-by">From the Leafout desk</p>
      </section>

      {(body || index) && (
        <div className={`pv-s-row${body && index ? '' : ' pv-s-row--solo'}`}>
          {body && <div className="pv-s-story">{body}</div>}
          {index && (
            <div className="pv-index-slot pv-s-watch pv-rise" style={rise()}>
              {index}
            </div>
          )}
        </div>
      )}

      {stories && <div className="pv-s-stories">{stories}</div>}
      {quote}
      {(numbers || honours || school) && (
        <div className="pv-s-pair">
          {numbers}
          {honours}
          {school}
        </div>
      )}
    </>
  );
}

function Lead(props: { page: PressPage; id: string; className: string; style: CSSProperties }) {
  const { lead } = props.page;
  return (
    <section className={props.className} style={props.style} aria-labelledby={props.id}>
      {lead.kicker && <p className="pv-kick">{lead.kicker}</p>}
      <h2 id={props.id} className="pv-hl">
        {lead.headline}
      </h2>
      {lead.deck && <p className="pv-deck">{lead.deck}</p>}
      <p className="pv-by">From the Leafout desk</p>
    </section>
  );
}

/* ───────────────────────── parts ───────────────────────── */

/** The thick-thin rule, built from fixed-height bars (3px, 3px gap, 1px) so no border is thicker than a hairline. */
function Rules({ thickFirst }: { thickFirst: boolean }) {
  return (
    <div className="pv-rules" aria-hidden="true">
      <span className={thickFirst ? 'pv-r3' : 'pv-r1'} />
      <span className={thickFirst ? 'pv-r1' : 'pv-r3'} />
    </div>
  );
}

function StorySection(props: { id: string; title: string; stories: PressStory[]; className: string }) {
  return (
    <section className={props.className} aria-labelledby={props.id}>
      <h2 id={props.id} className="pv-sec">
        {props.title}
      </h2>
      {props.stories.map((s) => (
        <article key={s.id} className="pv-story">
          <h3 className="pv-st-h">{s.heading}</h3>
          {s.meta && <p className="pv-st-d">{s.meta}</p>}
          {(s.text || s.dateline) && (
            <p className="pv-st-p">
              {s.dateline && <span className="pv-dl">{s.dateline} — </span>}
              {s.text}
            </p>
          )}
          {s.href && (
            <a className="pv-link" href={s.href} target="_blank" rel="noopener noreferrer">
              {s.linkLabel}
              <span aria-hidden="true">&nbsp;↗</span>
            </a>
          )}
        </article>
      ))}
    </section>
  );
}

function Numbers(props: { id: string; numbers: PressNumber[]; className: string; style: CSSProperties }) {
  return (
    <section className={`pv-box ${props.className}`} style={props.style} aria-labelledby={props.id}>
      <h2 id={props.id} className="pv-box-t">
        By the Numbers
      </h2>
      <dl className="pv-nums">
        {props.numbers.map((n, i) => (
          <div key={i} className={`pv-num${n.wide ? ' pv-num-wide' : ''}${n.first ? ' pv-num-first' : ''}`}>
            <dt>{n.label}</dt>
            <dd>{n.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function Honours(props: { id: string; honours: PressHonour[]; className: string; style: CSSProperties }) {
  return (
    <section className={`pv-box ${props.className}`} style={props.style} aria-labelledby={props.id}>
      <h2 id={props.id} className="pv-box-t">
        Honours
      </h2>
      <ul className="pv-hon">
        {props.honours.map((h) => (
          <li key={h.id}>
            <p className="pv-hon-h">{h.heading}</p>
            {h.meta && <p className="pv-hon-m">{h.meta}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}

function School(props: { id: string; school: PressSchool[]; className: string; style: CSSProperties }) {
  return (
    <section className={`pv-box ${props.className}`} style={props.style} aria-labelledby={props.id}>
      <h2 id={props.id} className="pv-box-t">
        Schooling
      </h2>
      <ul className="pv-hon">
        {props.school.map((s) => (
          <li key={s.id}>
            <p className="pv-hon-h">{s.heading}</p>
            {s.place && <p className="pv-hon-m">{s.place}</p>}
            {s.years && <p className="pv-sch-y">{s.years}</p>}
            {s.grade && <p className="pv-sch-y">{s.grade}</p>}
            {s.note && <p className="pv-hon-m">{s.note}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Ad({ ad }: { ad: PressAd }) {
  const inner = (
    <>
      <span className="pv-ad-k">{ad.key}</span>
      <span aria-hidden="true">·</span>
      <span className="pv-ad-v">{ad.value}</span>
    </>
  );
  return ad.href ? (
    <a className="pv-ad" href={ad.href} {...(ad.href.startsWith('mailto:') ? {} : { target: '_blank', rel: 'noopener noreferrer' })}>
      {inner}
    </a>
  ) : (
    <p className="pv-ad pv-ad--plain">{inner}</p>
  );
}
