/**
 * Reading a saved role back from localStorage. Storage is outside the app's control (another tab, an older build, a
 * person editing it by hand), so a stored role is checked like a server answer: an entry that is not a role is dropped,
 * a field that only decorates it is left out when it is wrong, and an apply link that is not a plain web address makes
 * the whole entry unusable (it is never linked or posted anywhere).
 *
 * The role itself is the `AiJob` the search showed, so the list still reads when the employer takes the posting down.
 */
import type { DiscoverWorkMode, Place, PlacePrecision, QuickFit } from '../../../shared/discover';
import type { AiFitWord, AiJob, AskCounts, AskMet, FoundVia, LevelWord } from '../../../shared/jobsearch';
import { hostOf, safeHref } from '../util';
import { arr, finite, isObj, isoTime, oneOf, readCite, text } from '../prepare/read';

const FIT_WORDS: readonly AiFitWord[] = ['strong', 'good', 'stretch'];
const LEVELS: readonly LevelWord[] = ['below', 'at', 'step_up', 'unclear'];
const VIA: readonly FoundVia[] = ['web', 'tayyari'];
const MODES: readonly DiscoverWorkMode[] = ['onsite', 'hybrid', 'remote', 'unknown'];
const PRECISIONS: readonly PlacePrecision[] = ['locality', 'city', 'country'];

const MAX_MET = 3;
const MAX_GAPS = 3;
/** A count of requirements: a read checks a dozen at most; anything near this is not a count. */
const MAX_COUNT = 200;

function readPlace(raw: unknown): Place | null {
  if (!isObj(raw)) return null;
  const label = text(raw.label, 200);
  if (!label) return null;
  const city = text(raw.city, 100);
  const locality = text(raw.locality, 100);
  const km = finite(raw.km);
  return {
    label,
    ...(city ? { city } : {}),
    ...(locality ? { locality } : {}),
    precision: oneOf(PRECISIONS, raw.precision) ?? 'city',
    ...(km !== undefined && km >= 0 ? { km } : {}),
  };
}

function readMet(raw: unknown): AskMet | null {
  if (!isObj(raw)) return null;
  const ask = text(raw.ask, 1000);
  const cite = readCite(raw.cite);
  return ask && cite ? { ask, cite, partly: raw.partly === true } : null;
}

const count = (v: unknown): number | undefined => {
  const n = finite(v);
  return n !== undefined && n >= 0 && n <= MAX_COUNT ? Math.floor(n) : undefined;
};

/** The pips' counts: as stored when they are counts, else worked out from the quotes the role kept. */
function readAsks(raw: unknown, met: readonly AskMet[], gaps: readonly string[]): AskCounts {
  if (isObj(raw)) {
    const m = count(raw.met);
    const p = count(raw.partly);
    const n = count(raw.notShown);
    if (m !== undefined && p !== undefined && n !== undefined) return { met: m, partly: p, notShown: n };
  }
  return { met: met.filter((a) => !a.partly).length, partly: met.filter((a) => a.partly).length, notShown: gaps.length };
}

function readQuickFit(raw: unknown): QuickFit {
  if (isObj(raw) && typeof raw.word === 'string') return raw as unknown as QuickFit;
  return { word: 'cant_tell', named: 0, matched: 0, notFound: [], reasons: [] };
}

/**
 * A saved role, or null when it lacks what a card must say: an id, a title, an employer, a place, a fit word, and a plain
 * web address to apply at. `fallbackTime` stands in for a listing time that was lost.
 */
export function readStoredJob(raw: unknown, fallbackTime: string): AiJob | null {
  if (!isObj(raw)) return null;
  const id = text(raw.id, 200);
  const title = text(raw.title, 300);
  const employer = text(raw.employer, 200);
  const applyUrl = text(raw.applyUrl, 2000);
  const place = readPlace(raw.place);
  const fit = oneOf(FIT_WORDS, raw.fit);
  if (!id || !title || !employer || !applyUrl || !place || !fit) return null;
  if (!safeHref(applyUrl)) return null;
  const applyHost = text(raw.applyHost, 200) ?? hostOf(applyUrl);
  if (!applyHost) return null;

  const foundVia = oneOf(VIA, raw.foundVia) ?? 'web';
  const postedAt = isoTime(raw.postedAt);
  const foundBy = foundVia === 'web' ? text(raw.foundBy, 400) : undefined;
  const levelQuote = text(raw.levelQuote, 600);
  const yearsAsked = finite(raw.yearsAsked);
  const salary = isObj(raw.salary) ? text(raw.salary.text, 200) : undefined;

  const met = arr(raw.met).flatMap((m) => {
    const a = readMet(m);
    return a ? [a] : [];
  }).slice(0, MAX_MET);
  const gaps = arr(raw.gaps).flatMap((g) => {
    const gap = text(g, 1000);
    return gap ? [gap] : [];
  }).slice(0, MAX_GAPS);

  return {
    id,
    title,
    employer,
    place,
    workMode: oneOf(MODES, raw.workMode) ?? 'unknown',
    ...(postedAt ? { postedAt } : {}),
    checkedAt: isoTime(raw.checkedAt) ?? fallbackTime,
    applyUrl,
    applyHost,
    foundVia,
    ...(foundBy ? { foundBy } : {}),
    fit,
    level: oneOf(LEVELS, raw.level) ?? 'unclear',
    ...(levelQuote ? { levelQuote } : {}),
    ...(yearsAsked !== undefined && yearsAsked >= 0 ? { yearsAsked } : {}),
    met,
    gaps,
    asks: readAsks(raw.asks, met, gaps),
    ...(raw.openLong === true ? { openLong: true } : {}),
    quickFit: readQuickFit(raw.quickFit),
    ...(salary ? { salary: { text: salary } } : {}),
  };
}
