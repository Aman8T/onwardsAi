import { promises as dns } from 'node:dns';
import { BlockList, isIP } from 'node:net';
import type { SourceKind } from '../../shared/types.ts';
import type { IngestLog, RawSource } from './types.ts';
import { isKnownPlatform, normalizeUrl } from './classify.ts';
import { findTerms } from './terms.ts';

/* ───────────────────────── constants ───────────────────────── */

const USER_AGENT =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
const PAGE_TIMEOUT_MS = 15_000;
const HTML_CAP_BYTES = 2 * 1024 * 1024;
const BUNDLE_CAP_BYTES = 3 * 1024 * 1024;
const MAX_REDIRECTS = 6;
const MAX_BUNDLES = 2;
const MAX_UI_STRINGS = 250;
const PAGE_TEXT_CHARS = 25_000;
const CONTENT_CHARS = 40_000;
/** A page with less visible text than this, plus module scripts, is treated as a client-rendered SPA. */
const THIN_TEXT_CHARS = 400;
/** Per-page caps so a hostile page cannot turn link/script bookkeeping into quadratic work. */
const MAX_LINKS = 3000;
const MAX_SCRIPT_URLS = 200;
const MAX_HEADINGS = 40;
const MAX_STRUCTURED = 6;
/** Bundle string literals examined (not kept) before extraction stops; real 1–3 MB bundles are far below this. */
const MAX_LITERALS_SCANNED = 150_000;

const LOGIN_WALL_LOVABLE = 'That is a private Lovable editor link — share the published .lovable.app URL instead';
const LINKEDIN_BLOCKED = 'LinkedIn blocks automated reading — paste your About section into Extra context instead';

/* ───────────────────────── fetching ───────────────────────── */

class FetchProblem extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'FetchProblem';
  }
}

interface Fetched {
  status: number;
  url: string;
  contentType: string;
  text: string;
  truncated: boolean;
}

/**
 * Addresses no public web link may point at: loopback, RFC 1918, link-local, CGNAT, multicast and
 * reserved IPv4, plus the IPv6 ranges that are not global unicast or that embed an IPv4 address
 * (::a.b.c.d, ::ffff:0:a.b.c.d, 64:ff9b::a.b.c.d, 2002:…, Teredo). Node checks IPv4-mapped IPv6
 * literals (::ffff:a.b.c.d, serialised by the URL parser as ::ffff:7f00:1) against the IPv4 rules.
 */
const PRIVATE_NETS = new BlockList();
for (const [net, bits] of [
  ['0.0.0.0', 8], ['10.0.0.0', 8], ['100.64.0.0', 10], ['127.0.0.0', 8], ['169.254.0.0', 16], ['172.16.0.0', 12],
  ['192.0.0.0', 24], ['192.168.0.0', 16], ['198.18.0.0', 15], ['224.0.0.0', 4], ['240.0.0.0', 4],
] as const) {
  PRIVATE_NETS.addSubnet(net, bits, 'ipv4');
}
for (const [net, bits] of [
  ['::', 96], ['::ffff:0:0:0', 96], ['64:ff9b::', 96], ['64:ff9b:1::', 48], ['100::', 64],
  ['fe80::', 10], ['fec0::', 10], ['fc00::', 7], ['ff00::', 8], ['2001::', 32], ['2001:db8::', 32], ['2002::', 16],
] as const) {
  PRIVATE_NETS.addSubnet(net, bits, 'ipv6');
}

/** True for an IP literal (either family) that must never be fetched. Anything that is not an address is refused too. */
function isPrivateAddress(address: string): boolean {
  const family = isIP(address);
  if (family === 0) return true;
  return PRIVATE_NETS.check(address, family === 6 ? 'ipv6' : 'ipv4');
}

/** Private by name (localhost, .local…) or an IP literal in a private range. Does not touch DNS. */
function isPrivateHost(hostname: string): boolean {
  const h = hostname.toLowerCase().replace(/^\[|\]$/g, '').replace(/\.$/, '');
  if (h === 'localhost' || h.endsWith('.localhost') || h.endsWith('.local') || h.endsWith('.internal') || h.endsWith('.lan') || h.endsWith('.home.arpa')) return true;
  return isIP(h) !== 0 && isPrivateAddress(h);
}

/**
 * True when the host is private by name or literal, or when any address it resolves to is private
 * (localtest.me, 127.0.0.1.nip.io, a custom name pointing at 169.254.169.254…). Runs before every
 * request hop. An unresolvable name is left to fetch so the usual "could not find that site" applies.
 */
async function resolvesPrivate(hostname: string): Promise<boolean> {
  if (isPrivateHost(hostname)) return true;
  const h = hostname.replace(/^\[|\]$/g, '');
  if (isIP(h) !== 0) return false; // a literal that passed isPrivateHost is public
  let addrs: Array<{ address: string; family: number }>;
  try {
    addrs = await dns.lookup(h, { all: true, verbatim: true });
  } catch {
    return false;
  }
  return addrs.some((a) => isPrivateAddress(a.address));
}

function friendlyNetworkError(err: unknown): string {
  if (process.env.INGEST_DEBUG) console.error('[ingest/web] fetch failed:', err);
  const e = err as { name?: string; message?: string; cause?: { code?: string; message?: string } };
  if (e?.name === 'TimeoutError' || e?.name === 'AbortError') return `The page took too long to respond (over ${PAGE_TIMEOUT_MS / 1000} seconds).`;
  const code = e?.cause?.code ?? '';
  if (code === 'ENOTFOUND' || code === 'EAI_AGAIN') return 'Could not find that site. Check the link for typos.';
  if (code === 'ECONNREFUSED') return 'That site refused the connection.';
  if (code === 'UND_ERR_CONNECT_TIMEOUT' || code === 'ETIMEDOUT') return 'Could not connect to that site (the connection timed out). Check the link.';
  if (/CERT|SSL|TLS|SELF_SIGNED|UNABLE_TO_VERIFY/i.test(code + ' ' + (e?.cause?.message ?? ''))) return "That site's security certificate is not valid.";
  if (e instanceof FetchProblem) return e.message;
  return 'Could not reach that site.';
}

function decodeBody(bytes: Uint8Array, contentType: string): string {
  const header = /charset=["']?([\w-]+)/i.exec(contentType)?.[1];
  let label = header;
  if (!label && /html|xml/i.test(contentType)) {
    const head = new TextDecoder('latin1').decode(bytes.subarray(0, 4096));
    label = /<meta[^<>]+charset=["']?([\w-]+)/i.exec(head)?.[1];
  }
  try {
    return new TextDecoder(label || 'utf-8').decode(bytes);
  } catch {
    return new TextDecoder('utf-8').decode(bytes);
  }
}

async function readCapped(res: Response, cap: number): Promise<{ bytes: Uint8Array; truncated: boolean }> {
  if (!res.body) return { bytes: new Uint8Array(0), truncated: false };
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  let truncated = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (total + value.length > cap) {
      chunks.push(value.subarray(0, cap - total));
      total = cap;
      truncated = true;
      await reader.cancel().catch(() => undefined);
      break;
    }
    chunks.push(value);
    total += value.length;
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    bytes.set(c, offset);
    offset += c.length;
  }
  return { bytes, truncated };
}

/** One retry for connection resets and dropped streams (never for timeouts, DNS, HTTP errors or a cancelled request). */
async function fetchTextRetry(startUrl: string, cap: number, accept: string, signal?: AbortSignal): Promise<Fetched> {
  try {
    return await fetchText(startUrl, cap, accept, signal);
  } catch (err) {
    const e = err as { name?: string; message?: string; cause?: { code?: string } };
    const code = e?.cause?.code ?? '';
    const transient =
      !signal?.aborted &&
      !(err instanceof FetchProblem) &&
      e?.name !== 'TimeoutError' &&
      e?.name !== 'AbortError' &&
      (/^(ECONNRESET|EPIPE|ECONNABORTED|UND_ERR_SOCKET|UND_ERR_ABORTED)$/.test(code) || /terminated|socket|reset/i.test(e?.message ?? ''));
    if (!transient) throw err;
    await new Promise((resolve) => setTimeout(resolve, 400));
    return await fetchText(startUrl, cap, accept, signal);
  }
}

/** GET with manual redirects (so every hop is resolved and checked), a hard timeout, the caller's abort signal and a body size cap. */
async function fetchText(startUrl: string, cap: number, accept: string, signal?: AbortSignal): Promise<Fetched> {
  const timeout = AbortSignal.timeout(PAGE_TIMEOUT_MS);
  const sig = signal ? AbortSignal.any([signal, timeout]) : timeout;
  let url = startUrl;
  for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
    const u = new URL(url);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') throw new FetchProblem('That link redirects somewhere that cannot be read.');
    if (await resolvesPrivate(u.hostname)) throw new FetchProblem('That address is private. Only public links can be read.');
    sig.throwIfAborted();
    const res = await fetch(url, {
      redirect: 'manual',
      signal: sig,
      headers: {
        'User-Agent': USER_AGENT,
        Accept: accept,
        'Accept-Language': 'en-US,en;q=0.9',
      },
    });
    if (res.status >= 300 && res.status < 400 && res.headers.get('location')) {
      await res.body?.cancel().catch(() => undefined);
      url = new URL(res.headers.get('location')!, url).toString();
      continue;
    }
    const contentType = res.headers.get('content-type') ?? '';
    const { bytes, truncated } = await readCapped(res, cap);
    return { status: res.status, url, contentType, text: decodeBody(bytes, contentType), truncated };
  }
  throw new FetchProblem('That link redirects too many times.');
}

/* ───────────────────────── HTML parsing (no dependencies) ───────────────────────── */
// Everything here is linear in the page size, even on hostile input (2 MB of "<a ", "<!--" or one
// 2 MB tag): tag patterns never run past the next "<", block closers are searched forward from each
// opener instead of lazily from every later opener, and attributes are read in one forward pass.

const NAMED_ENTITIES: Record<string, string> = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', mdash: '—', ndash: '–', hellip: '…',
  rsquo: '’', lsquo: '‘', rdquo: '”', ldquo: '“', copy: '©', reg: '®', trade: '™', middot: '·', bull: '•',
  rarr: '→', larr: '←', times: '×', laquo: '«', raquo: '»', euro: '€', pound: '£', deg: '°', amp2: '&',
};

function decodeEntities(s: string): string {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z][a-z0-9]*);/gi, (match, ent: string) => {
    if (ent[0] === '#') {
      const code = ent[1] === 'x' || ent[1] === 'X' ? parseInt(ent.slice(2), 16) : parseInt(ent.slice(1), 10);
      if (Number.isFinite(code) && code > 0 && code <= 0x10ffff) {
        try {
          return String.fromCodePoint(code);
        } catch {
          return match;
        }
      }
      return match;
    }
    return NAMED_ENTITIES[ent.toLowerCase()] ?? match;
  });
}

/**
 * Walks opener…closer blocks left to right. The closer is searched forward from the end of each
 * opener; once a closer regex finds nothing, later openers that need it are skipped, so an
 * unterminated block costs one scan instead of one per opener. `cb` returns false to stop early.
 * The regexes must be global and are reused across calls (lastIndex is managed here).
 */
function eachBlock(
  html: string,
  openRe: RegExp,
  closerFor: (open: RegExpExecArray) => RegExp,
  cb: (open: RegExpExecArray, inner: string, end: number) => boolean | void,
): void {
  const exhausted = new Set<RegExp>();
  openRe.lastIndex = 0;
  try {
    for (let m = openRe.exec(html); m; m = openRe.exec(html)) {
      const closeRe = closerFor(m);
      if (exhausted.has(closeRe)) continue;
      const openEnd = m.index + m[0].length;
      closeRe.lastIndex = openEnd;
      const c = closeRe.exec(html);
      if (!c) {
        exhausted.add(closeRe);
        continue;
      }
      const end = c.index + c[0].length;
      if (cb(m, html.slice(openEnd, c.index), end) === false) return;
      openRe.lastIndex = end;
    }
  } finally {
    openRe.lastIndex = 0;
  }
}

/** Removes every closed opener…closer block. An unterminated block is left in place (as the old lazy regex did). */
function stripBetween(html: string, openRe: RegExp, closeRe: RegExp, replacement: string): string {
  let out = '';
  let pos = 0;
  eachBlock(html, openRe, () => closeRe, (m, _inner, end) => {
    out += html.slice(pos, m.index) + replacement;
    pos = end;
  });
  return pos === 0 ? html : out + html.slice(pos);
}

const BLOCK_RES = new Map<string, [RegExp, RegExp]>();
function stripBlocks(html: string, tags: string[]): string {
  let out = html;
  for (const tag of tags) {
    let pair = BLOCK_RES.get(tag);
    if (!pair) {
      pair = [new RegExp(`<${tag}\\b[^<>]*>`, 'gi'), new RegExp(`<\\/${tag}\\s*>`, 'gi')];
      BLOCK_RES.set(tag, pair);
    }
    out = stripBetween(out, pair[0], pair[1], ' ');
  }
  return out;
}

const isSpace = (c: string): boolean => c === ' ' || c === '\t' || c === '\n' || c === '\r' || c === '\f';

/** Attributes of one tag, first occurrence wins, read in a single forward pass (a regex here is quadratic on a huge tag). */
function parseAttrs(tag: string): Record<string, string> {
  const attrs: Record<string, string> = {};
  const n = tag.length;
  let i = 1;
  while (i < n && !isSpace(tag[i]!) && tag[i] !== '>') i++; // tag name (also "/x" and "!x")
  while (i < n) {
    while (i < n && (isSpace(tag[i]!) || tag[i] === '/')) i++;
    if (i >= n || tag[i] === '>') break;
    const nameStart = i;
    while (i < n && !isSpace(tag[i]!) && tag[i] !== '=' && tag[i] !== '>' && tag[i] !== '/') i++;
    const key = tag.slice(nameStart, i).toLowerCase();
    let j = i;
    while (j < n && isSpace(tag[j]!)) j++;
    let value: string | undefined;
    if (tag[j] === '=') {
      j++;
      while (j < n && isSpace(tag[j]!)) j++;
      const q = tag[j];
      if (q === '"' || q === "'") {
        const close = tag.indexOf(q, j + 1);
        value = tag.slice(j + 1, close === -1 ? n : close);
        j = close === -1 ? n : close + 1;
      } else {
        const valueStart = j;
        while (j < n && !isSpace(tag[j]!) && tag[j] !== '>') j++;
        value = tag.slice(valueStart, j);
      }
      i = j;
    }
    if (key && value !== undefined && !(key in attrs)) attrs[key] = decodeEntities(value);
  }
  return attrs;
}

const squash = (s: string): string => s.replace(/[\s ]+/g, ' ').trim();

/**
 * The inside of a tag after its name, tolerant of ">" in quoted values (Tailwind's "[&>*]:mb-0").
 * The free branch excludes "<" so an attempt at a tag that never closes stops at the next tag
 * instead of running to the end of the document.
 */
const IN_TAG = `(?:"[^"]*"|'[^']*'|[^<>"'])*`;
const RE_BR = new RegExp(`<(?:br|hr)\\b${IN_TAG}>`, 'gi');
const RE_HEADING_OPEN = new RegExp(`<h([1-6])\\b${IN_TAG}>`, 'gi');
const RE_LI_OPEN = new RegExp(`<li\\b${IN_TAG}>`, 'gi');
const RE_CELL_OPEN = new RegExp(`<(?:td|th)\\b${IN_TAG}>`, 'gi');
/** Any tag: "<" must be followed by a letter, "/" or "!" so "a < b" survives as text. */
const RE_ANY_TAG = new RegExp(`<[/!]?[a-zA-Z]${IN_TAG}>`, 'g');
const RE_META = new RegExp(`<meta\\b${IN_TAG}>`, 'gi');
const RE_A_OPEN = new RegExp(`<a\\b${IN_TAG}>`, 'gi');
const RE_SCRIPT_TAG = /<script\b[^<>]*>/gi;
const RE_LINK_TAG = /<link\b[^<>]*>/gi;
const RE_COMMENT_OPEN = /<!--/g;
const RE_COMMENT_CLOSE = /-->/g;
const RE_TITLE_OPEN = /<title\b[^<>]*>/gi;
const RE_TITLE_CLOSE = /<\/title\s*>/gi;
const RE_SCRIPT_BLOCK_OPEN = /<script\b[^<>]*>/gi;
const RE_SCRIPT_BLOCK_CLOSE = /<\/script\s*>/gi;
const RE_H_BLOCK_OPEN = new RegExp(`<h([1-3])\\b${IN_TAG}>`, 'gi');
const RE_H_BLOCK_CLOSE = [/<\/h1\s*>/gi, /<\/h2\s*>/gi, /<\/h3\s*>/gi] as const;

function htmlToText(html: string): string {
  const s = html
    .replace(RE_BR, '\n')
    .replace(/<\/(p|div|section|article|aside|li|ul|ol|tr|table|blockquote|pre|h[1-6]|figure|figcaption|dd|dt|dl|form|fieldset|main|details|summary|address)\s*>/gi, '\n')
    .replace(RE_HEADING_OPEN, (_m, n: string) => `\n${'#'.repeat(Math.min(Number(n), 4))} `)
    .replace(RE_LI_OPEN, '\n- ')
    .replace(RE_CELL_OPEN, ' | ')
    .replace(RE_ANY_TAG, ' ');
  const lines = decodeEntities(s)
    .split('\n')
    .map(squash)
    .filter((l) => l && !/^[-|•·]+$/.test(l));
  const out: string[] = [];
  for (const line of lines) if (out[out.length - 1] !== line) out.push(line);
  return out.join('\n');
}

interface PageInfo {
  title: string;
  description: string;
  ogTitle: string;
  ogDescription: string;
  siteName: string;
  author: string;
  /** <meta name="generator"> ("Astro v4", "Lovable"…). */
  generator: string;
  headings: Array<{ level: number; text: string }>;
  text: string;
  links: string[];
  structured: string[];
  moduleScripts: string[];
  plainScripts: string[];
  html: string;
}

function parsePage(rawHtml: string, pageUrl: string): PageInfo {
  const html = stripBetween(rawHtml, RE_COMMENT_OPEN, RE_COMMENT_CLOSE, '');

  let rawTitle = '';
  eachBlock(html, RE_TITLE_OPEN, () => RE_TITLE_CLOSE, (_m, inner) => {
    rawTitle = inner;
    return false;
  });
  const title = squash(decodeEntities(rawTitle));

  const meta = new Map<string, string>();
  for (const m of html.matchAll(RE_META)) {
    const a = parseAttrs(m[0]);
    const key = (a.name ?? a.property ?? '').toLowerCase();
    if (key && a.content && !meta.has(key)) meta.set(key, squash(a.content));
  }

  // JSON-LD can carry a clean description of a person or product.
  const structured: string[] = [];
  eachBlock(html, RE_SCRIPT_BLOCK_OPEN, () => RE_SCRIPT_BLOCK_CLOSE, (m, inner) => {
    if (!/^application\/ld\+json$/i.test((parseAttrs(m[0]).type ?? '').trim())) return;
    try {
      const data: unknown = JSON.parse(inner);
      const nodes = Array.isArray(data) ? data : [data, ...(((data as { '@graph'?: unknown[] })['@graph']) ?? [])];
      for (const node of nodes) {
        if (!node || typeof node !== 'object') continue;
        const n = node as Record<string, unknown>;
        const type = String(Array.isArray(n['@type']) ? n['@type'].join('/') : n['@type'] ?? '');
        if (!/person|profilepage|website|organization|softwareapplication|webapplication|creativework/i.test(type)) continue;
        const bits = ['name', 'jobTitle', 'description', 'url']
          .map((k) => (typeof n[k] === 'string' ? `${k}: ${squash(String(n[k])).slice(0, 240)}` : ''))
          .filter(Boolean);
        if (Array.isArray(n.sameAs)) bits.push(`sameAs: ${n.sameAs.filter((x) => typeof x === 'string').slice(0, 8).join(', ')}`);
        if (bits.length) structured.push(`${type || 'Thing'}: ${bits.join(' | ')}`);
        if (structured.length >= MAX_STRUCTURED) break;
      }
    } catch {
      /* ignore malformed JSON-LD */
    }
    if (structured.length >= MAX_STRUCTURED) return false;
  });

  // Scripts: collect before they are stripped.
  const moduleScripts: string[] = [];
  const plainScripts: string[] = [];
  for (const m of html.matchAll(RE_SCRIPT_TAG)) {
    if (moduleScripts.length + plainScripts.length >= MAX_SCRIPT_URLS) break;
    const a = parseAttrs(m[0]);
    if (!a.src) continue;
    if ((a.type ?? '').toLowerCase() === 'module') moduleScripts.push(a.src);
    else if (!a.type || /javascript/i.test(a.type)) plainScripts.push(a.src);
  }
  for (const m of html.matchAll(RE_LINK_TAG)) {
    if (moduleScripts.length + plainScripts.length >= MAX_SCRIPT_URLS) break;
    const a = parseAttrs(m[0]);
    if ((a.rel ?? '').toLowerCase().split(/\s+/).includes('modulepreload') && a.href) moduleScripts.push(a.href);
  }

  const cleaned = stripBlocks(html, ['script', 'style', 'noscript', 'svg', 'template', 'iframe', 'canvas', 'head']);

  const headings: Array<{ level: number; text: string }> = [];
  const seenHeadings = new Set<string>();
  eachBlock(cleaned, RE_H_BLOCK_OPEN, (m) => RE_H_BLOCK_CLOSE[Number(m[1]) - 1]!, (m, inner) => {
    const text = squash(decodeEntities(inner.replace(RE_ANY_TAG, ' ')));
    if (!text || text.length > 200 || seenHeadings.has(text)) return;
    seenHeadings.add(text);
    headings.push({ level: Number(m[1]), text });
    if (headings.length >= MAX_HEADINGS) return false;
  });

  let text = htmlToText(stripBlocks(cleaned, ['nav', 'footer', 'header']));
  if (text.length < 300) {
    // Some sites wrap everything in <header>; fall back to the lenient version.
    const lenient = htmlToText(cleaned);
    if (lenient.length > text.length) text = lenient;
  }

  const links: string[] = [];
  for (const m of cleaned.matchAll(RE_A_OPEN)) {
    if (links.length >= MAX_LINKS) break;
    const href = parseAttrs(m[0]).href;
    if (!href || /^(?:mailto:|tel:|javascript:|#)/i.test(href)) continue;
    try {
      links.push(new URL(href, pageUrl).toString());
    } catch {
      /* ignore bad hrefs */
    }
  }

  return {
    title,
    description: meta.get('description') ?? '',
    ogTitle: meta.get('og:title') ?? '',
    ogDescription: meta.get('og:description') ?? '',
    siteName: meta.get('og:site_name') ?? '',
    author: meta.get('author') ?? '',
    generator: meta.get('generator') ?? '',
    headings,
    text,
    links,
    structured,
    moduleScripts,
    plainScripts,
    html,
  };
}

/* ───────────────────────── SPA bundle analysis ───────────────────────── */

const LIBRARY_SIGNATURES: Array<[string, RegExp]> = [
  ['React', /react\.(?:transitional\.)?element|__reactFiber|__reactProps|reactjs\.org\/docs\/error-decoder|react-dom/],
  ['Vite', /\/assets\/index-[\w-]+\.js|__vite|vite\/modulepreload-polyfill|vite-legacy/],
  ['Supabase', /supabase\.co|@supabase\/|supabase-js|GoTrueClient|sb-[a-z0-9]+-auth-token/],
  ['Firebase', /firebaseio\.com|firebaseapp\.com|identitytoolkit\.googleapis|FirebaseError|@firebase\//],
  ['Stripe', /js\.stripe\.com|stripe\.com\/v3|@stripe\/|loadStripe|\bpk_(?:live|test)_/],
  ['OpenAI', /api\.openai\.com|openai\.com\/v1|"gpt-(?:4|5|3\.5)[\w.-]*"/],
  ['Anthropic', /api\.anthropic\.com|anthropic-version|"claude-(?:3|opus|sonnet|haiku)[\w.-]*"/],
  ['Gemini', /generativelanguage\.googleapis\.com|google\/gemini|"gemini-[\w.-]+"/],
  ['Clerk', /clerk\.(?:com|accounts\.dev)|@clerk\/|ClerkProvider|clerk-js/],
  ['Auth0', /auth0\.com|@auth0\/|Auth0Provider/],
  ['React Router', /react-router|@remix-run\/router|BrowserRouter|createBrowserRouter|useNavigate\(\) may be used/],
  ['TanStack Query', /@tanstack\/|QueryClientProvider|\bqueryFn:/],
  ['Framer Motion', /framer-motion|AnimatePresence|data-projection-id|\bwhile(?:Hover|Tap|InView):/],
  ['Three.js', /WebGLRenderer|three\.js|@react-three\/|THREE\./],
  ['Recharts', /recharts/],
  ['Radix UI', /@radix-ui|data-radix-/],
  ['shadcn/ui', /ring-offset-background|text-primary-foreground|text-muted-foreground|bg-muted\/50/],
  ['Lucide', /lucide-react|["' ]lucide-[a-z0-9-]+|\blucide\b/],
  ['Zod', /invalid_type|ZodError|invalid_string|too_small/],
  ['React Hook Form', /react-hook-form|useFormContext|FormProvider/],
  ['Mapbox', /mapbox/i],
  ['Google Maps', /maps\.googleapis\.com|google\.maps\.|@react-google-maps|googlemaps/],
  ['Leaflet', /leaflet-container|react-leaflet/],
  ['Chart.js', /chart\.js|chartjs/i],
  ['Vercel Analytics', /vercel-insights|_vercel\/insights|va\.vercel-scripts|@vercel\/analytics/],
  ['PostHog', /posthog/i],
  ['Sentry', /sentry\.io|@sentry\/|Sentry\.init/],
  ['Google Analytics', /googletagmanager\.com|google-analytics\.com|gtag\(/],
  ['Resend', /api\.resend\.com|resend\.com\/emails/],
  ['ElevenLabs', /elevenlabs/i],
  ['Twilio', /twilio\.com/],
  ['Plaid', /plaid\.com|react-plaid-link/],
  ['i18next', /i18next|react-i18next/],
  ['date-fns', /date-fns/],
  ['Sonner', /data-sonner|sonner/],
  ['Axios', /axios\.|isAxiosError|AxiosError/],
  ['GSAP', /gsap\.|GreenSock/],
  ['Lottie', /lottie/i],
];

/** Frameworks and site builders that leave a fingerprint in the served HTML itself. */
const HTML_SIGNATURES: Array<[string, RegExp]> = [
  ['Next.js', /\/_next\/static|__NEXT_DATA__|id="__next"/],
  ['Gatsby', /___gatsby|\/page-data\/|gatsby-image/],
  ['Nuxt', /__NUXT__|\/_nuxt\//],
  ['Astro', /astro-island|astro-[a-z0-9]{8}\b|name="generator"[^<>]{0,160}content="Astro/i],
  ['Remix', /__remixContext/],
  ['SvelteKit', /__sveltekit|data-sveltekit/],
  ['Docusaurus', /docusaurus/i],
  ['WordPress', /wp-content\/|wp-includes\//],
  ['Webflow', /data-wf-(?:page|site)|assets\.website-files\.com|webflow\.com/],
  ['Framer', /framerusercontent\.com|data-framer-|framer\.com\/m\//],
  ['Wix', /wixstatic\.com/],
  ['Squarespace', /static1\.squarespace\.com|squarespace-cdn/],
  ['Shopify', /cdn\.shopify\.com/],
];

/** Analytics and trackers say little about a person's skills, so they stay out of the quick signals. */
const NON_SKILL_SIGNALS = new Set(['Google Analytics', 'Vercel Analytics', 'PostHog', 'Sentry', 'Stripe', 'Sonner']);

/** Hosts that appear in every bundle because of library internals, not because the app calls them. */
const NOISE_HOSTS = [
  'w3.org', 'reactjs.org', 'react.dev', 'mozilla.org', 'github.com', 'fb.me', 'facebook.com', 'schema.org', 'npmjs.com',
  'stackoverflow.com', 'tailwindcss.com', 'radix-ui.com', 'ui.shadcn.com', 'wikipedia.org', 'unpkg.com', 'jsdelivr.net',
  'googleapis.com/css', 'fonts.googleapis.com', 'fonts.gstatic.com', 'example.com', 'localhost', 'lovable.dev', 'xmlns.com',
  'tanstack.com', 'zod.dev', 'redux.js.org', 'reactrouter.com', 'remix.run', 'developer.chrome.com', 'web.dev', 'ecma-international.org',
  'unicode.org', 'iana.org', 'ietf.org', 'whatwg.org', 'caniuse.com', 'sonner.emilkowal.ski', 'lucide.dev', 'recharts.org',
];

const COPY_NOISE =
  /\b(?:React|hooks?|props?|prototype|undefined|null|NaN|callback|constructor|Symbol|iterable|invariant|arguments?|Invalid|Cannot|Unexpected|deprecated|Uncaught|Minified|Radix|Warning|TypeError|RangeError|ReferenceError|Provider|Context|DOM|SVG|HTML|JSON|Promise|WebGL|Failed to execute|authenticators?|credentials?|ceremony|pubKeyCredParams|assertion|attestation|cryptographic|ArrayBuffer|Uint8Array|Iterator|Observable|Proxy|Reflect|WeakMap)\b/;

function unescapeJs(raw: string, quote: string): string {
  if (!raw.includes('\\')) return raw;
  if (quote === '"') {
    try {
      return JSON.parse(`"${raw}"`) as string;
    } catch {
      /* fall through */
    }
  }
  return raw
    .replace(/\\u([0-9a-fA-F]{4})/g, (_m, h: string) => String.fromCharCode(parseInt(h, 16)))
    .replace(/\\x([0-9a-fA-F]{2})/g, (_m, h: string) => String.fromCharCode(parseInt(h, 16)))
    .replace(/\\n/g, ' ')
    .replace(/\\t/g, ' ')
    .replace(/\\(['"`\\/])/g, '$1');
}

/** Short strings handed straight to a UI prop: children, placeholder, title… High confidence. */
function looksLikePropCopy(s: string): boolean {
  if (s.length < 2 || s.length > 200) return false;
  if (!/^[A-Za-z0-9]/.test(s)) return false;
  if (/[{}();=<>\\^~$`@]|\[|\]|_|::|:\/\//.test(s)) return false;
  if (!/[A-Za-z]{2}/.test(s)) return false;
  if (COPY_NOISE.test(s)) return false;
  if (!s.includes(' ')) {
    // single token: only keep it when it reads like a label ("Sign In", "Next", "Dashboard")
    return /^[A-Z][a-z][A-Za-z'’!?.,&-]*$/.test(s) && s.length >= 3;
  }
  // Tailwind/class lists and identifier-ish word lists: lowercase start and no sentence punctuation.
  if (!/^[A-Z0-9]/.test(s) && !/[.,!?'’:]/.test(s)) return false;
  return true;
}

/** Any quoted string that reads like a sentence: 3–140 chars, has a space, starts with a letter. */
function looksLikeSentence(s: string): boolean {
  if (s.length < 3 || s.length > 140) return false;
  if (!/^[A-Za-z]/.test(s) || !s.includes(' ')) return false;
  if (/[{}();=<>\\^~$`@]|\[|\]|_|::|:\/\/|\.(?:js|css|tsx?|json)\b/.test(s)) return false;
  if (COPY_NOISE.test(s)) return false;
  if (!/^[A-Z]/.test(s) && !/[.,!?'’:]/.test(s)) return false;
  const tokens = s.split(/\s+/);
  const classy = tokens.filter((t) => /^[a-z0-9]+(?:[-:/][a-z0-9[\].%#]+)+$/.test(t)).length;
  if (classy / tokens.length >= 0.4) return false;
  if (tokens.some((t) => /[a-z][A-Z]/.test(t) && !/^[A-Z]/.test(t))) return false; // camelCase identifiers
  return true;
}

interface BundleFindings {
  propStrings: string[];
  sentences: Array<{ text: string; score: number }>;
  routes: string[];
  tables: string[];
  functions: string[];
  hosts: Array<[string, number]>;
  libraries: string[];
}

function analyzeBundle(js: string, extraHtml: string): BundleFindings {
  const propSeen = new Set<string>();
  const propStrings: string[] = [];
  const propRe =
    /(?:children|placeholder|title|label|alt|description|subtitle|heading|tagline|text|question|answer|quote|"aria-label"|ariaLabel)\s*:\s*(["'`])((?:\\.|(?!\1)[^\\\n]){2,200}?)\1/g;
  let scanned = 0;
  for (const m of js.matchAll(propRe)) {
    if (++scanned > MAX_LITERALS_SCANNED) break;
    if (m[1] === '`' && m[2]!.includes('${')) continue;
    const s = squash(unescapeJs(m[2]!, m[1]!));
    if (propSeen.has(s) || !looksLikePropCopy(s)) continue;
    propSeen.add(s);
    propStrings.push(s);
  }

  const sentenceSeen = new Set<string>();
  const sentences: Array<{ text: string; score: number }> = [];
  const strRe = /"((?:[^"\\\n]|\\.){3,160})"|'((?:[^'\\\n]|\\.){3,160})'|`((?:[^`\\\n$]|\\.){3,160})`/g;
  const len = js.length || 1;
  scanned = 0;
  for (const m of js.matchAll(strRe)) {
    if (++scanned > MAX_LITERALS_SCANNED) break;
    const quote = m[1] !== undefined ? '"' : m[2] !== undefined ? "'" : '`';
    const s = squash(unescapeJs(m[1] ?? m[2] ?? m[3] ?? '', quote));
    if (sentenceSeen.has(s) || propSeen.has(s) || !looksLikeSentence(s)) continue;
    sentenceSeen.add(s);
    const words = s.split(/\s+/).length;
    const score =
      (words >= 3 ? 2 : 0) + (words >= 6 ? 1 : 0) + (/[.!?]$/.test(s) ? 2 : 0) + (/^[A-Z]/.test(s) ? 1 : 0) + 1.5 * ((m.index ?? 0) / len);
    sentences.push({ text: s, score });
  }

  const routes = new Set<string>();
  for (const m of js.matchAll(/\bpath:\s*"(\/[A-Za-z0-9_\-/:*]{0,60}|\*)"/g)) {
    const r = m[1]!;
    if (r !== '*' && r !== '/') routes.add(r);
    if (routes.size >= 25) break;
  }

  const tables = new Set<string>();
  for (const m of js.matchAll(/\.from\(\s*"([a-z_][a-z0-9_-]{1,40})"\s*\)/g)) {
    tables.add(m[1]!);
    if (tables.size >= 20) break;
  }
  const functions = new Set<string>();
  for (const m of js.matchAll(/\.(?:rpc|invoke)\(\s*"([A-Za-z_][\w-]{1,40})"/g)) {
    functions.add(m[1]!);
    if (functions.size >= 15) break;
  }

  const hostCounts = new Map<string, number>();
  for (const m of js.matchAll(/https?:\/\/([a-z0-9][a-z0-9.-]*\.[a-z]{2,})(?=[/"'`?])/gi)) {
    const host = m[1]!.toLowerCase();
    if (NOISE_HOSTS.some((n) => host === n || host.endsWith('.' + n))) continue;
    hostCounts.set(host, (hostCounts.get(host) ?? 0) + 1);
  }

  const haystack = js + '\n' + extraHtml;
  const libraries = LIBRARY_SIGNATURES.filter(([, re]) => re.test(haystack)).map(([name]) => name);
  // Tailwind: lots of utility class strings.
  const utilityHits = js.match(/\b(?:bg|px|py|text|rounded|border|gap|items|justify|space-[xy])-[a-z0-9[\]/.-]+/g)?.length ?? 0;
  if (utilityHits >= 80 && /\bflex\b/.test(js)) libraries.push('Tailwind CSS');

  return {
    propStrings,
    sentences,
    routes: [...routes],
    tables: [...tables],
    functions: [...functions],
    hosts: [...hostCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12),
    libraries,
  };
}

function mergeUnique<T>(into: T[], from: T[], key: (t: T) => string): void {
  const seen = new Set(into.map(key));
  for (const item of from) {
    const k = key(item);
    if (!seen.has(k)) {
      seen.add(k);
      into.push(item);
    }
  }
}

/* ───────────────────────── helpers ───────────────────────── */

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`;

function displaySrc(url: string): string {
  return url.replace(/^https?:\/\//i, '').replace(/\/$/, '');
}

function sameOrigin(a: string, b: string): boolean {
  try {
    return new URL(a).origin === new URL(b).origin;
  } catch {
    return false;
  }
}

function emptySource(
  id: SourceKind,
  kind: SourceKind,
  label: string,
  kindLabel: string,
  url: string,
  src: string,
  error: string,
  extra: Partial<RawSource> = {},
): RawSource {
  return { id, kind, label, url, src, kindLabel, content: '', facts: {}, quickSignals: [], error, ...extra };
}

function isBlockedStatus(status: number): boolean {
  return status === 401 || status === 403 || status === 999 || status === 451;
}

function looksLikeBotWall(page: PageInfo): boolean {
  const t = page.title.toLowerCase();
  return (
    /^(just a moment|attention required|access denied|are you a robot|robot or human|verify you are human|security check|ddos protection|checking your browser|one more step|pardon our interruption|please verify|bot verification)/.test(t) ||
    (/captcha/.test(t) && page.text.length < 600) ||
    (/cf-chl|challenge-platform|_cf_chl_opt|g-recaptcha-response|hcaptcha\.com\/1\/api/.test(page.html.slice(0, 20_000)) &&
      page.text.length < 600)
  );
}

/* ───────────────────────── the ingester ───────────────────────── */

export async function ingestWeb(
  url: string,
  kind: SourceKind,
  label: string,
  kindLabel: string,
  log: IngestLog,
  signal?: AbortSignal,
): Promise<RawSource> {
  const parsed = normalizeUrl(url);
  const fullUrl = parsed ? parsed.toString() : url;
  const src = displaySrc(parsed ? parsed.toString() : url);
  const fail = (error: string, extra: Partial<RawSource> = {}): RawSource =>
    emptySource(kind, kind, label, kindLabel, fullUrl, src, error, extra);

  if (!parsed) return fail('That does not look like a valid web link.');
  const host = parsed.hostname.toLowerCase().replace(/^www\./, '');
  const isLinkedIn = host === 'linkedin.com' || host.endsWith('.linkedin.com') || host === 'lnkd.in';

  // Lovable editor links (lovable.dev/projects/…) are the Lovable app itself, not the user's published app.
  if ((host === 'lovable.dev' || host.endsWith('.lovable.dev')) && /^\/projects?\b/i.test(parsed.pathname)) {
    log(`Opened ${src}`);
    return fail(LOGIN_WALL_LOVABLE);
  }

  let page: Fetched;
  try {
    page = await fetchTextRetry(fullUrl, HTML_CAP_BYTES, 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', signal);
  } catch (err) {
    if (signal?.aborted) return fail('Cancelled.');
    if (isLinkedIn) return fail(LINKEDIN_BLOCKED);
    return fail(friendlyNetworkError(err));
  }

  const finalHost = new URL(page.url).hostname.toLowerCase().replace(/^www\./, '');
  const loginRedirect = /authwall|\/login|\/signin|\/sign-in|\/signup|\/uas\/|auth-bridge|\/auth\b/i.test(new URL(page.url).pathname + new URL(page.url).search);

  if (isLinkedIn) {
    if (isBlockedStatus(page.status) || page.status === 429 || page.status >= 400 || loginRedirect || !/linkedin\.com$/.test(finalHost)) {
      return fail(LINKEDIN_BLOCKED);
    }
  }
  if ((host.endsWith('.lovable.app') || host.endsWith('.lovableproject.com')) && (finalHost === 'lovable.dev' || finalHost.endsWith('.lovable.dev'))) {
    return fail(LOGIN_WALL_LOVABLE);
  }

  if ((page.status === 404 || page.status === 410) && (host.endsWith('.lovable.app') || host.endsWith('.lovableproject.com'))) {
    return fail('That Lovable app was not found. It may be unpublished or private. Publish it and share the .lovable.app URL.');
  }
  if (page.status === 404 || page.status === 410) return fail(`That page was not found (HTTP ${page.status}). Check the link.`);
  if (isBlockedStatus(page.status)) return fail(`That site blocks automated reading or needs a login (HTTP ${page.status}).`);
  if (page.status === 429) return fail('That site is rate limiting requests. Try again in a few minutes.');
  if (page.status >= 500) return fail(`That site returned a server error (HTTP ${page.status}).`);
  if (page.status >= 400) return fail(`That page could not be opened (HTTP ${page.status}).`);

  log(`Opened ${src}`);

  const ct = page.contentType.toLowerCase();
  const isHtml = !ct || /html|xhtml|xml/.test(ct) || /^\s*<(?:!doctype|html)/i.test(page.text.slice(0, 200));
  if (!isHtml) {
    if (/application\/pdf/.test(ct)) return fail('That link points to a PDF, which cannot be read here. Paste its text into Extra context instead.');
    if (/^text\/|json/.test(ct)) {
      const text = page.text.trim().slice(0, PAGE_TEXT_CHARS);
      return {
        id: kind, kind, label, url: fullUrl, src, kindLabel,
        content: `# ${src}\n\n${text}`,
        facts: { title: '', description: '', spa: false, libraries: [], words: text.split(/\s+/).length, status: page.status },
        quickSignals: findTerms(text, 6),
      };
    }
    return fail(`That link is not a web page (${ct.split(';')[0]}).`);
  }

  const info = parsePage(page.text, page.url);
  if (looksLikeBotWall(info)) return fail('That site shows a bot check, so it could not be read.');
  if (isLinkedIn && /^(sign up|log in|sign in|join linkedin)/i.test(info.title)) return fail(LINKEDIN_BLOCKED);

  const hasScripts = info.moduleScripts.length > 0 || info.plainScripts.length > 0;
  const thin = info.text.length < THIN_TEXT_CHARS;
  const spa = thin && info.moduleScripts.length > 0;

  /* SPA: read the JS bundle for human UI copy and the libraries it ships. */
  let findings: BundleFindings | undefined;
  const bundleNotes: string[] = [];
  if (thin && hasScripts) {
    const candidates: string[] = [];
    const seenCandidates = new Set<string>();
    const sources = info.moduleScripts.length > 0 ? info.moduleScripts : info.plainScripts;
    for (const s of sources) {
      try {
        const abs = new URL(s, page.url).toString();
        if (sameOrigin(abs, page.url) && /\.m?js(?:$|\?)/i.test(abs) && !seenCandidates.has(abs)) {
          seenCandidates.add(abs);
          candidates.push(abs);
        }
      } catch {
        /* ignore */
      }
    }
    // The main entry bundle holds the app; keep the order of appearance but prefer "index" files first.
    candidates.sort((a, b) => Number(/\/index[-.]/i.test(b)) - Number(/\/index[-.]/i.test(a)));
    if (candidates.length > 0) {
      log(spa ? 'SPA detected, reading app bundle' : 'Thin page, reading its scripts');
      const merged: BundleFindings = { propStrings: [], sentences: [], routes: [], tables: [], functions: [], hosts: [], libraries: [] };
      let read = 0;
      for (const bundleUrl of candidates.slice(0, MAX_BUNDLES)) {
        if (signal?.aborted) break;
        try {
          const js = await fetchTextRetry(bundleUrl, BUNDLE_CAP_BYTES, '*/*', signal);
          if (js.status >= 400 || !js.text) continue;
          read++;
          const f = analyzeBundle(js.text, info.html);
          mergeUnique(merged.propStrings, f.propStrings, (s) => s);
          mergeUnique(merged.sentences, f.sentences, (s) => s.text);
          mergeUnique(merged.routes, f.routes, (s) => s);
          mergeUnique(merged.tables, f.tables, (s) => s);
          mergeUnique(merged.functions, f.functions, (s) => s);
          mergeUnique(merged.hosts, f.hosts, (s) => s[0]);
          mergeUnique(merged.libraries, f.libraries, (s) => s);
          if (js.truncated) bundleNotes.push(`${bundleUrl.split('/').pop()} was larger than ${BUNDLE_CAP_BYTES / 1024 / 1024} MB and was read partially.`);
        } catch {
          /* a bundle that fails to load is not fatal */
        }
      }
      if (read > 0) findings = merged;
      else bundleNotes.push('The app bundle could not be downloaded, so only the page shell was read.');
    }
  }

  // Libraries visible in the HTML itself (works for non-SPA pages too).
  const htmlLibraries = LIBRARY_SIGNATURES.filter(([name, re]) => name !== 'Vite' && name !== 'React' && re.test(info.html)).map(([n]) => n);
  const htmlFrameworks = HTML_SIGNATURES.filter(([, re]) => re.test(info.html)).map(([n]) => n);
  if ((info.html.match(/\b(?:px|py|mt|mb|bg|text|rounded|gap)-(?:\d|\[|[a-z]+-\d)/g)?.length ?? 0) >= 15) htmlFrameworks.push('Tailwind CSS');
  const libraries: string[] = [];
  mergeUnique(libraries, htmlFrameworks, (s) => s);
  mergeUnique(libraries, findings?.libraries ?? [], (s) => s);
  mergeUnique(libraries, htmlLibraries, (s) => s);
  // On Medium, Kaggle, LinkedIn… the stack belongs to the platform, not to the person.
  if (isKnownPlatform(finalHost) || isKnownPlatform(host)) libraries.length = 0;
  // Lovable's default social image, tagger and generator meta are a reliable fingerprint.
  const builtWithLovable =
    /lovable\.dev\/opengraph-image|lovable-tagger|gptengineer/i.test(info.html) || /lovable/i.test(info.generator) || host.endsWith('.lovable.app');

  // Rank sentence-like strings: prop copy first, then the most sentence-like leftovers.
  const uiStrings: string[] = [];
  if (findings) {
    uiStrings.push(...findings.propStrings);
    const extras = [...findings.sentences].sort((a, b) => b.score - a.score).map((s) => s.text);
    for (const s of extras) {
      if (uiStrings.length >= MAX_UI_STRINGS) break;
      if (!uiStrings.includes(s)) uiStrings.push(s);
    }
    uiStrings.length = Math.min(uiStrings.length, MAX_UI_STRINGS);
  }

  /* markdown content */
  // Prefer the social title when the <title> is only a fragment of it ("Medium" vs "Dan Abramov – Medium").
  const title =
    info.ogTitle && info.title && info.ogTitle.length > info.title.length && info.ogTitle.includes(info.title)
      ? info.ogTitle
      : info.title || info.ogTitle || info.siteName || host;
  const description = info.description || info.ogDescription;
  const out: string[] = [`# ${title}`, '', `URL: ${page.url}`];
  if (page.url !== fullUrl && finalHost !== host) out.push(`(redirected from ${src})`);
  if (description) out.push(`Description: ${description}`);
  if (info.ogTitle && info.ogTitle !== info.title) out.push(`Social title: ${info.ogTitle}`);
  if (info.ogDescription && info.ogDescription !== description) out.push(`Social description: ${info.ogDescription}`);
  if (info.siteName) out.push(`Site name: ${info.siteName}`);
  if (info.author) out.push(`Author: ${info.author}`);
  if (builtWithLovable) out.push('Built with: Lovable (Vite + React)');

  if (info.structured.length) {
    out.push('', '## Structured data', ...info.structured.map((s) => `- ${s}`));
  }

  // The page text already carries headings as "## …" lines; only list them separately when it does not.
  const headingsInText = info.headings.filter((h) => info.text.includes(h.text)).length;
  if (info.headings.length && headingsInText < info.headings.length * 0.8) {
    out.push('', '## Headings', ...info.headings.map((h) => `- h${h.level}: ${h.text}`));
  }

  const pageText = info.text.length > PAGE_TEXT_CHARS ? info.text.slice(0, PAGE_TEXT_CHARS) + '\n[…truncated]' : info.text;
  if (pageText) out.push('', '## Page text', pageText);
  else if (spa) out.push('', '## Page text', '(The page is rendered by JavaScript, so its HTML has no visible text.)');

  if (uiStrings.length) {
    out.push('', '## UI copy found in the app bundle', ...uiStrings.map((s) => `- ${s}`));
  }
  if (findings?.routes.length) out.push('', '## Routes in the app', findings.routes.join(', '));
  if (findings && (findings.tables.length || findings.functions.length)) {
    out.push('', '## Backend hints');
    if (findings.tables.length) out.push(`- Tables or storage buckets queried: ${findings.tables.join(', ')}`);
    if (findings.functions.length) out.push(`- Functions or RPCs called: ${findings.functions.join(', ')}`);
  }
  if (findings?.hosts.length) {
    out.push('', '## External services referenced in the bundle', findings.hosts.map(([h]) => h).join(', '));
  }
  if (libraries.length) out.push('', '## Libraries and services detected', libraries.join(', '));

  // Outbound links
  const outbound = info.links.filter((l) => {
    try {
      return new URL(l).hostname.replace(/^www\./, '') !== finalHost;
    } catch {
      return false;
    }
  });
  const outboundHosts = new Map<string, number>();
  for (const l of outbound) {
    const h = new URL(l).hostname.replace(/^www\./, '');
    outboundHosts.set(h, (outboundHosts.get(h) ?? 0) + 1);
  }
  if (outbound.length) {
    const notable = [
      ...new Set(
        outbound.filter((l) => /(?:github\.com|linkedin\.com|twitter\.com|x\.com|medium\.com|dev\.to|kaggle\.com|youtube\.com|substack\.com|behance\.net|dribbble\.com|lovable\.app|vercel\.app|netlify\.app|huggingface\.co)/i.test(l)),
      ),
    ].slice(0, 12);
    out.push(
      '',
      '## Links',
      `- ${plural(outbound.length, 'outbound link')} to ${plural(outboundHosts.size, 'site')}: ${[...outboundHosts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([h, n]) => (n > 1 ? `${h} (${n})` : h)).join(', ')}`,
    );
    if (notable.length) out.push(`- Notable: ${notable.join(', ')}`);
  }

  if (bundleNotes.length || page.truncated || isLinkedIn) {
    out.push('', '## Notes on this data');
    if (isLinkedIn) out.push('- This is the public, signed-out view of the LinkedIn profile, so it can be partial. Ignore the sign-in form text.');
    if (page.truncated) out.push(`- The page was larger than ${HTML_CAP_BYTES / 1024 / 1024} MB and was read partially.`);
    for (const n of bundleNotes) out.push(`- ${n}`);
  }

  const content = out.join('\n').slice(0, CONTENT_CHARS);
  const words = (pageText + ' ' + uiStrings.join(' ')).split(/\s+/).filter(Boolean).length;

  if (!description && !info.title && !pageText && uiStrings.length === 0) {
    return fail('No readable content was found on that page.', { facts: { spa, status: page.status } });
  }

  const quickSignals: string[] = [];
  if (kind === 'lovable') quickSignals.push('Live app');
  const libCap = spa ? 8 : quickSignals.length + 3; // static pages: a few frameworks, then skills found in the text
  for (const lib of libraries) {
    if (lib === 'Vite' || quickSignals.length >= libCap) continue;
    if (!spa && NON_SKILL_SIGNALS.has(lib)) continue;
    quickSignals.push(lib);
  }
  if (!spa) {
    // On LinkedIn the page text is mostly other people's names and chrome, so only trust the description.
    const termSource = isLinkedIn ? description : `${description}\n${pageText}`;
    for (const term of findTerms(termSource, 8)) {
      if (quickSignals.length >= 8) break;
      if (!quickSignals.includes(term)) quickSignals.push(term);
    }
  }

  const topServices = libraries.filter((l) => !['React', 'Vite', 'Tailwind CSS', 'Radix UI', 'shadcn/ui', 'Lucide', 'Sonner', 'Zod'].includes(l)).slice(0, 3);
  if (spa && findings) {
    log([`Found ${plural(uiStrings.length, 'UI string')}`, ...topServices].join(', '));
  } else {
    log(`Read ${words.toLocaleString('en-US')} words${info.headings.length ? `, ${plural(info.headings.length, 'heading')}` : ''}`);
  }

  return {
    id: kind,
    kind,
    label,
    url: fullUrl,
    src,
    kindLabel,
    content,
    facts: {
      title,
      description,
      spa,
      libraries,
      words,
      status: page.status,
      finalUrl: page.url,
      headings: info.headings.length,
      outboundLinks: outbound.length,
      uiStrings: uiStrings.length,
      routes: findings?.routes ?? [],
      builtWithLovable,
    },
    quickSignals: quickSignals.slice(0, 8),
  };
}

/** Internals exposed for the deterministic check scripts (timing and SSRF harnesses); not part of the ingest API. */
export const webInternals = { parsePage, analyzeBundle, isPrivateHost, isPrivateAddress, resolvesPrivate };
