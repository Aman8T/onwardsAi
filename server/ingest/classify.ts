import type { SourceKind } from '../../shared/types.ts';

export interface ClassifiedUrl {
  /** Normalised absolute URL (https:// added, hash removed). */
  url: string;
  kind: SourceKind;
  /** Short label: "GitHub", "Lovable", "Kaggle"… */
  label: string;
  /** What the link is: "GitHub profile", "Lovable app", "Web page"… */
  kindLabel: string;
}

/** Normalise user input into a URL, or null when it is not a plausible http(s) address. */
export function normalizeUrl(raw: string): URL | null {
  let s = (raw ?? '').trim();
  // Strip wrapping punctuation people paste along with links: <url>, "url", (url), trailing "," or "."
  s = s.replace(/^[<("'`\s]+/, '').replace(/[>)"'`,;.\s]+$/, '');
  if (!s || /\s/.test(s)) return null;

  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) {
    // explicit scheme, validated below
  } else if (/^(?:mailto|tel|javascript|data|file|ftp|sms|blob):/i.test(s)) {
    return null;
  } else {
    s = 'https://' + s.replace(/^\/\//, '');
  }

  let u: URL;
  try {
    u = new URL(s);
  } catch {
    return null;
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
  if (u.username || u.password) return null;
  const host = u.hostname.toLowerCase();
  // Needs a real dot-separated host ("hello" or "localhost" is not a link). The URL parser has
  // already lowercased it and converted international names to punycode.
  const isIpv6 = host.startsWith('[');
  if (!isIpv6 && !/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(host)) return null;
  u.hash = '';
  return u;
}

const stripWww = (host: string): string => host.replace(/^www\./, '');
const hostIs = (host: string, domain: string): boolean => host === domain || host.endsWith('.' + domain);

const PORTFOLIO_DOMAINS = [
  'behance.net',
  'dribbble.com',
  'github.io',
  'vercel.app',
  'netlify.app',
  'framer.website',
  'framer.ai',
  'read.cv',
  'notion.site',
  'carrd.co',
  'webflow.io',
  'pages.dev',
  'wixsite.com',
  'squarespace.com',
  'about.me',
  'bento.me',
  'super.so',
];

const FRIENDLY_LABELS: Record<string, string> = {
  'kaggle.com': 'Kaggle',
  'leetcode.com': 'LeetCode',
  'stackoverflow.com': 'Stack Overflow',
  'stackexchange.com': 'Stack Exchange',
  'huggingface.co': 'Hugging Face',
  'gitlab.com': 'GitLab',
  'bitbucket.org': 'Bitbucket',
  'npmjs.com': 'npm',
  'pypi.org': 'PyPI',
  'youtube.com': 'YouTube',
  'youtu.be': 'YouTube',
  'twitter.com': 'X',
  'x.com': 'X',
  'codepen.io': 'CodePen',
  'codesandbox.io': 'CodeSandbox',
  'hackerrank.com': 'HackerRank',
  'codechef.com': 'CodeChef',
  'codeforces.com': 'Codeforces',
  'geeksforgeeks.org': 'GeeksforGeeks',
  'wellfound.com': 'Wellfound',
  'producthunt.com': 'Product Hunt',
  'figma.com': 'Figma',
  'medium.com': 'Blog',
  'dev.to': 'Blog',
  'hashnode.com': 'Blog',
  'hashnode.dev': 'Blog',
  'substack.com': 'Blog',
  'wordpress.com': 'Blog',
  'blogspot.com': 'Blog',
  'ghost.io': 'Blog',
};

const MULTI_PART_TLDS = new Set([
  'co.uk', 'org.uk', 'ac.uk', 'gov.uk', 'co.in', 'org.in', 'ac.in', 'net.in', 'gov.in', 'com.au', 'net.au',
  'org.au', 'edu.au', 'co.nz', 'co.jp', 'com.br', 'com.mx', 'com.sg', 'com.hk', 'com.tr', 'co.za', 'com.cn',
  'co.kr', 'com.ar', 'com.ng', 'co.il', 'com.pk', 'com.bd',
]);

/** "docs.kaggle.com" → "Kaggle", "my-site.co.uk" → "My-site". */
function secondLevelLabel(host: string): string {
  const parts = stripWww(host).split('.');
  let sld: string;
  if (parts.length >= 3 && MULTI_PART_TLDS.has(parts.slice(-2).join('.'))) sld = parts[parts.length - 3] ?? parts[0]!;
  else if (parts.length >= 2) sld = parts[parts.length - 2] ?? parts[0]!;
  else sld = parts[0]!;
  return sld.charAt(0).toUpperCase() + sld.slice(1);
}

function friendlyLabel(host: string): string {
  if (/^\d+\.\d+\.\d+\.\d+$/.test(host) || host.startsWith('[')) return host; // IP literal
  for (const [domain, label] of Object.entries(FRIENDLY_LABELS)) {
    if (hostIs(host, domain)) return label;
  }
  return secondLevelLabel(host);
}

/** True for sites where the person is a user of someone else's platform (Medium, Kaggle, LinkedIn…). */
export function isKnownPlatform(hostname: string): boolean {
  const host = stripWww(hostname.toLowerCase());
  return (
    hostIs(host, 'linkedin.com') ||
    hostIs(host, 'github.com') ||
    Object.keys(FRIENDLY_LABELS).some((domain) => hostIs(host, domain))
  );
}

export function classifyUrl(raw: string): ClassifiedUrl | null {
  const u = normalizeUrl(raw);
  if (!u) return null;
  const host = stripWww(u.hostname.toLowerCase());
  const segments = u.pathname.split('/').filter(Boolean);

  // Drop a bare trailing slash so "https://overreacted.io" stays tidy.
  const url = u.pathname === '/' && !u.search ? u.origin : u.toString();
  const make = (kind: SourceKind, label: string, kindLabel: string): ClassifiedUrl => ({ url, kind, label, kindLabel });

  if (host === 'github.com') {
    const reserved = new Set(['orgs', 'topics', 'sponsors', 'marketplace', 'features', 'about', 'pricing', 'settings', 'login']);
    const isRepo = segments.length >= 2 && !reserved.has(segments[0]!.toLowerCase());
    return make('github', 'GitHub', isRepo ? 'GitHub repo' : 'GitHub profile');
  }

  if (hostIs(host, 'lovable.app') || hostIs(host, 'lovable.dev') || hostIs(host, 'lovableproject.com')) {
    return make('lovable', 'Lovable', 'Lovable app');
  }

  if (hostIs(host, 'linkedin.com') || host === 'lnkd.in') {
    return make('linkedin', 'LinkedIn', 'LinkedIn profile');
  }

  if (PORTFOLIO_DOMAINS.some((d) => hostIs(host, d)) || /portfolio/i.test(u.hostname + u.pathname)) {
    return make('portfolio', 'Portfolio', 'Portfolio site');
  }

  return make('link', friendlyLabel(host), 'Web page');
}
