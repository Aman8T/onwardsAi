import type { IngestLog, RawSource } from './types.ts';
import { normalizeUrl } from './classify.ts';
import { countTerms, findTerms } from './terms.ts';

/* ───────────────────────── constants ───────────────────────── */

const API = 'https://api.github.com';
/** Hard budget of API calls per profile (the unauthenticated limit is 60 per hour). */
const MAX_CALLS = 14;
const REQUEST_TIMEOUT_MS = 12_000;
const README_CHARS = 2500;
const DETAILED_REPOS = 8;
const README_REPOS = 5;
const TREE_REPOS = 4;
const RATE_LIMIT_MSG = 'GitHub rate limit reached — set GITHUB_TOKEN to raise it';

/* ───────────────────────── API shapes (only what we read) ───────────────────────── */

interface GhUser {
  login: string;
  name: string | null;
  bio: string | null;
  company: string | null;
  location: string | null;
  blog: string | null;
  twitter_username?: string | null;
  followers: number;
  following: number;
  public_repos: number;
  public_gists?: number;
  created_at: string;
  type?: string;
  hireable?: boolean | null;
}

interface GhRepo {
  name: string;
  full_name: string;
  description: string | null;
  fork: boolean;
  archived?: boolean;
  language: string | null;
  stargazers_count: number;
  forks_count: number;
  topics?: string[];
  pushed_at: string | null;
  created_at?: string;
  homepage: string | null;
  size: number;
  default_branch: string;
  license?: { spdx_id?: string | null; name?: string | null } | null;
  open_issues_count?: number;
  owner?: { login: string };
}

interface GhTree {
  tree?: Array<{ path: string; type: string }>;
  truncated?: boolean;
}

interface GhEvent {
  type: string;
  repo: { name: string };
  created_at: string;
  payload?: {
    action?: string;
    size?: number;
    commits?: unknown[];
    ref_type?: string;
    pull_request?: { merged?: boolean };
  };
}

interface RepoInsights {
  inspected: true;
  files: number;
  tests: boolean;
  testFiles: number;
  ci: string[];
  docker: boolean;
  manifests: string[];
  docs: boolean;
  truncated: boolean;
}

/* ───────────────────────── tiny HTTP client with a call budget ───────────────────────── */

interface GhResult {
  status: number;
  ok: boolean;
  json?: unknown;
  text?: string;
  rateLimited: boolean;
  /** The request never produced a response (offline, DNS, timeout) or was over budget. */
  failed: boolean;
}

class GitHubClient {
  calls = 0;
  rateLimited = false;
  private readonly token = process.env.GITHUB_TOKEN?.trim() || undefined;
  /** The caller's abort signal (client disconnected): no further calls are made once it fires. */
  private readonly signal: AbortSignal | undefined;

  constructor(signal?: AbortSignal) {
    this.signal = signal;
  }

  get authenticated(): boolean {
    return !!this.token;
  }

  async get(path: string, accept = 'application/vnd.github+json'): Promise<GhResult> {
    if (this.signal?.aborted || this.calls >= MAX_CALLS) return { status: 0, ok: false, rateLimited: false, failed: true };
    this.calls++;
    const headers: Record<string, string> = {
      'User-Agent': 'tayyari-poc',
      Accept: accept,
      'X-GitHub-Api-Version': '2022-11-28',
    };
    if (this.token) headers.Authorization = `Bearer ${this.token}`;

    const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
    let res: Response;
    try {
      res = await fetch(API + path, { headers, signal: this.signal ? AbortSignal.any([this.signal, timeout]) : timeout });
    } catch {
      return { status: 0, ok: false, rateLimited: false, failed: true };
    }

    const isRaw = accept.includes('raw');
    let body = '';
    try {
      body = await res.text();
    } catch {
      /* treat as empty */
    }

    let rateLimited = false;
    if (res.status === 429) rateLimited = true;
    if (res.status === 403) {
      const remaining = res.headers.get('x-ratelimit-remaining');
      if (remaining === '0' || /rate limit|abuse/i.test(body)) rateLimited = true;
    }
    if (rateLimited) this.rateLimited = true;

    const result: GhResult = { status: res.status, ok: res.ok, rateLimited, failed: false };
    if (res.ok) {
      if (isRaw) result.text = body;
      else {
        try {
          result.json = JSON.parse(body);
        } catch {
          result.ok = false;
          result.failed = true;
        }
      }
    }
    return result;
  }
}

/* ───────────────────────── small helpers ───────────────────────── */

const plural = (n: number, word: string, pluralWord?: string): string =>
  `${n} ${n === 1 ? word : (pluralWord ?? word + 's')}`;
const fmtInt = (n: number): string => n.toLocaleString('en-US');

function dateOnly(iso?: string | null): string {
  return iso ? iso.slice(0, 10) : 'unknown';
}

function ago(iso?: string | null, now = Date.now()): string {
  if (!iso) return 'unknown';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return 'unknown';
  const days = Math.max(0, Math.floor((now - t) / 86_400_000));
  if (days < 1) return 'today';
  if (days < 14) return `${plural(days, 'day')} ago`;
  if (days < 60) return `${plural(Math.round(days / 7), 'week')} ago`;
  if (days < 730) return `${plural(Math.round(days / 30.4), 'month')} ago`;
  return `${plural(Math.round(days / 365.25), 'year')} ago`;
}

function monthsSince(iso?: string | null, now = Date.now()): number {
  const t = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(t)) return 120;
  return Math.max(0, (now - t) / (86_400_000 * 30.4));
}

function topCounts(values: Iterable<string>, limit: number): Array<[string, number]> {
  const map = new Map<string, number>();
  for (const v of values) map.set(v, (map.get(v) ?? 0) + 1);
  return [...map.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit);
}

function cleanReadme(raw: string): string {
  // Fenced code blocks are kept verbatim; only the prose around them is cleaned.
  const parts = raw.replace(/\r\n?/g, '\n').split(/(```[\s\S]*?```)/g);
  const prose = (p: string): string =>
    p
      .replace(/<!--[\s\S]*?-->/g, '')
      .replace(/^[ \t]*\[[^\]]+\]:[ \t]*\S.*$/gm, '') // reference-style link definitions
      .replace(/\[!\[[^\]]*\](?:\([^)]*\)|\[[^\]]*\])\](?:\([^)]*\)|\[[^\]]*\])/g, '') // linked badges
      .replace(/!\[[^\]]*\](?:\([^)]*\)|\[[^\]]*\])/g, '') // images
      .replace(/<img\b[^>]*>/gi, '')
      .replace(/<\/?(?:p|div|br|a|b|i|em|strong|picture|source|h[1-6]|center|span|details|summary|sup|sub|table|tr|td|th|thead|tbody|kbd)\b[^>]*>/gi, ' ')
      .replace(/\[([^\]]+)\](?:\([^)]*\)|\[[^\]]*\])/g, '$1') // keep link text, drop the target
      .replace(/[ \t]+/g, ' ')
      .replace(/ *\n */g, '\n');
  let s = parts
    .map((p, i) => (i % 2 === 1 ? p : prose(p)))
    .join('')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  if (s.length > README_CHARS) {
    s = s.slice(0, README_CHARS);
    const cut = s.lastIndexOf('\n');
    if (cut > README_CHARS * 0.6) s = s.slice(0, cut);
    s += '\n[…truncated]';
  }
  return s;
}

/** Prefix each line so README headings never collide with our own markdown structure. */
const quoteBlock = (text: string): string =>
  text
    .split('\n')
    .map((l) => (l.trim() ? `> ${l}` : '>'))
    .join('\n');

function analyzeTree(tree: GhTree): RepoInsights {
  const entries = tree.tree ?? [];
  const blobs = entries.filter((e) => e.type === 'blob').map((e) => e.path);
  const testRe =
    /(^|\/)(tests?|__tests__|spec|specs|e2e|__mocks__)\/|\.(test|spec)\.[a-z0-9]+$|(^|\/)test_[^/]+\.py$|_test\.(go|py|rb|rs)$|Test\.java$/i;
  const testFiles = blobs.filter((p) => testRe.test(p)).length;

  const ci: string[] = [];
  if (blobs.some((p) => /^\.github\/workflows\/[^/]+\.ya?ml$/i.test(p))) ci.push('GitHub Actions');
  if (blobs.some((p) => /^\.circleci\//i.test(p))) ci.push('CircleCI');
  if (blobs.some((p) => /(^|\/)\.gitlab-ci\.ya?ml$/i.test(p))) ci.push('GitLab CI');
  if (blobs.some((p) => /(^|\/)\.travis\.ya?ml$/i.test(p))) ci.push('Travis CI');
  if (blobs.some((p) => /(^|\/)Jenkinsfile$/i.test(p))) ci.push('Jenkins');
  if (blobs.some((p) => /(^|\/)azure-pipelines\.ya?ml$/i.test(p))) ci.push('Azure Pipelines');
  if (blobs.some((p) => /(^|\/)bitbucket-pipelines\.ya?ml$/i.test(p))) ci.push('Bitbucket Pipelines');

  const manifestRe =
    /^(package\.json|pyproject\.toml|requirements\.txt|setup\.py|Pipfile|Cargo\.toml|go\.mod|pom\.xml|build\.gradle(?:\.kts)?|Gemfile|composer\.json|pubspec\.yaml|deno\.json|Package\.swift)$/;
  const manifests = [...new Set(blobs.filter((p) => !p.includes('/') && manifestRe.test(p)))];

  return {
    inspected: true,
    files: blobs.length,
    tests: testFiles > 0,
    testFiles,
    ci,
    docker: blobs.some((p) => /(^|\/)(Dockerfile(\.[\w-]+)?|docker-compose\.ya?ml|compose\.ya?ml)$/i.test(p)),
    manifests,
    docs:
      blobs.some((p) => /^docs?\//i.test(p) || /^(mkdocs\.yml|CONTRIBUTING\.md)$/i.test(p)) ||
      blobs.filter((p) => /\.mdx?$/i.test(p)).length > 3,
    truncated: !!tree.truncated,
  };
}

function parseGithubTarget(input: string): { owner: string; repo?: string } | null {
  const trimmed = (input ?? '').trim();
  if (/^@?[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/.test(trimmed)) return { owner: trimmed.replace(/^@/, '') };
  const u = normalizeUrl(trimmed);
  if (!u) return null;
  const host = u.hostname.toLowerCase().replace(/^www\./, '');
  if (host !== 'github.com') return null;
  const [owner, repo] = u.pathname.split('/').filter(Boolean);
  if (!owner || !/^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(owner)) return null;
  const reserved = new Set(['orgs', 'topics', 'sponsors', 'marketplace', 'features', 'about', 'pricing', 'settings', 'login', 'explore']);
  if (reserved.has(owner.toLowerCase())) return null;
  return { owner, repo: repo ? repo.replace(/\.git$/i, '') : undefined };
}

/* ───────────────────────── ranking ───────────────────────── */

function repoScore(r: GhRepo): number {
  const recency = Math.max(0, 24 - monthsSince(r.pushed_at)); // 0–24, fresh repos win
  const size = Math.min(10, Math.log2((r.size || 0) + 1)); // 0–10, tiny repos lose
  return r.stargazers_count * 3 + recency + (r.description ? 4 : 0) + size - (r.archived ? 6 : 0);
}

/* ───────────────────────── activity summary ───────────────────────── */

interface ActivitySummary {
  total: number;
  spanDays: number;
  pushes: number;
  commits: number | null;
  prsOpened: number;
  prsMerged: number;
  externalPrs: number;
  issuesOpened: number;
  comments: number;
  reviews: number;
  reposCreated: number;
  stars: number;
  activeRepos: Array<[string, number]>;
  pushLanguages: Array<[string, number]>;
  latest?: string;
}

function summarizeEvents(events: GhEvent[], login: string, languageByRepo: Map<string, string>): ActivitySummary {
  const owner = login.toLowerCase();
  const times = events.map((e) => Date.parse(e.created_at)).filter((n) => !Number.isNaN(n));
  const pushes = events.filter((e) => e.type === 'PushEvent');
  // Newer event payloads may no longer carry commit counts, so track whether any did.
  let commits = 0;
  let hasCommitInfo = false;
  for (const p of pushes) {
    const n = p.payload?.size ?? p.payload?.commits?.length;
    if (typeof n === 'number') {
      commits += n;
      hasCommitInfo = true;
    }
  }
  const prEvents = events.filter((e) => e.type === 'PullRequestEvent');
  const opened = prEvents.filter((e) => e.payload?.action === 'opened');
  const pushRepos = pushes.map((p) => languageByRepo.get(p.repo.name.toLowerCase())).filter((l): l is string => !!l);

  return {
    total: events.length,
    spanDays: times.length > 1 ? Math.max(1, Math.round((Math.max(...times) - Math.min(...times)) / 86_400_000)) : 0,
    pushes: pushes.length,
    commits: hasCommitInfo ? commits : null,
    prsOpened: opened.length,
    prsMerged: prEvents.filter((e) => e.payload?.action === 'closed' && e.payload?.pull_request?.merged).length,
    externalPrs: opened.filter((e) => e.repo.name.split('/')[0]?.toLowerCase() !== owner).length,
    issuesOpened: events.filter((e) => e.type === 'IssuesEvent' && e.payload?.action === 'opened').length,
    comments: events.filter((e) => e.type === 'IssueCommentEvent' || e.type === 'CommitCommentEvent').length,
    reviews: events.filter((e) => e.type === 'PullRequestReviewEvent').length,
    reposCreated: events.filter((e) => e.type === 'CreateEvent' && e.payload?.ref_type === 'repository').length,
    stars: events.filter((e) => e.type === 'WatchEvent').length,
    activeRepos: topCounts(events.map((e) => e.repo.name), 5),
    pushLanguages: topCounts(pushRepos, 4),
    latest: events[0]?.created_at,
  };
}

/* ───────────────────────── the ingester ───────────────────────── */

interface State {
  login: string;
  user?: GhUser;
  repos?: GhRepo[];
  focus?: GhRepo;
  profileReadme?: string;
  readmes: Map<string, string>;
  /** Repos where GitHub answered 404 or served an empty README (as opposed to a fetch that was rate-limited or failed). */
  noReadme: Set<string>;
  insights: Map<string, RepoInsights>;
  activity?: ActivitySummary;
  notes: string[];
  /** Original repos by rank (profile-README repo excluded). */
  ranked: GhRepo[];
  /** The repos described in detail. */
  detailed: GhRepo[];
}

export async function ingestGithub(url: string, log: IngestLog, signal?: AbortSignal): Promise<RawSource> {
  const target = parseGithubTarget(url);
  const base = {
    id: 'github',
    kind: 'github' as const,
    label: 'GitHub',
    url: target ? `https://github.com/${target.owner}${target.repo ? '/' + target.repo : ''}` : url,
    kindLabel: target?.repo ? 'GitHub repo' : 'GitHub profile',
  };
  if (!target) {
    return {
      ...base,
      src: 'github.com',
      content: '',
      facts: {},
      quickSignals: [],
      error: 'That does not look like a GitHub profile or repository link.',
    };
  }

  const src = `github.com/${target.owner}`;
  const client = new GitHubClient(signal);
  const state: State = { login: target.owner, readmes: new Map(), noReadme: new Set(), insights: new Map(), notes: [], ranked: [], detailed: [] };
  const finish = (error?: string): RawSource => buildSource(base, src, state, client, error);

  /* 1. profile */
  const userRes = await client.get(`/users/${encodeURIComponent(target.owner)}`);
  if (userRes.rateLimited) return finish(RATE_LIMIT_MSG);
  if (userRes.status === 404) return finish(`GitHub user "${target.owner}" was not found. Check the link.`);
  if (!userRes.ok || !userRes.json) {
    return finish(userRes.failed ? 'Could not reach GitHub. Check the connection and try again.' : `GitHub returned an unexpected response (${userRes.status}).`);
  }
  const user = userRes.json as GhUser;
  state.user = user;
  state.login = user.login || target.owner;
  log(`Fetched profile, ${plural(user.public_repos ?? 0, 'public repo')}`);

  /* 2. repositories */
  const reposRes = await client.get(`/users/${encodeURIComponent(state.login)}/repos?per_page=100&sort=pushed`);
  if (reposRes.rateLimited) return finish(RATE_LIMIT_MSG);
  if (!reposRes.ok || !Array.isArray(reposRes.json)) {
    state.notes.push('The repository list could not be fetched.');
    return finish(reposRes.failed ? 'Could not reach GitHub while listing repositories.' : `GitHub could not list repositories (${reposRes.status}).`);
  }
  const repos = reposRes.json as GhRepo[];
  state.repos = repos;

  // A /owner/repo link always puts that repo in the detailed set.
  if (target.repo) {
    const want = target.repo.toLowerCase();
    state.focus = repos.find((r) => r.name.toLowerCase() === want);
    if (!state.focus) {
      const one = await client.get(`/repos/${encodeURIComponent(state.login)}/${encodeURIComponent(target.repo)}`);
      if (one.ok && one.json) {
        state.focus = one.json as GhRepo;
      } else if (one.status === 404) {
        state.notes.push(`The repository ${state.login}/${target.repo} was not found; showing the profile instead.`);
      }
    }
  }

  /* 3. rank */
  const profileRepoName = state.login.toLowerCase();
  const originals = repos.filter((r) => !r.fork);
  const ranked = originals
    .filter((r) => r.name.toLowerCase() !== profileRepoName)
    .sort((a, b) => repoScore(b) - repoScore(a));
  const detailed: GhRepo[] = [];
  if (state.focus) detailed.push(state.focus);
  for (const r of ranked) {
    if (detailed.length >= DETAILED_REPOS) break;
    if (!detailed.some((d) => d.name === r.name)) detailed.push(r);
  }
  state.ranked = ranked;
  state.detailed = detailed;
  log('Ranked repos by recency and stars');

  /* 4. READMEs, file trees, events and the profile README, all in parallel */
  const profileReadmeRepo = repos.find((r) => r.name.toLowerCase() === profileRepoName && !r.fork);
  const tasks: Array<Promise<void>> = [];

  for (const repo of detailed.slice(0, README_REPOS)) {
    tasks.push(
      (async () => {
        const res = await client.get(`/repos/${encodeURIComponent(state.login)}/${encodeURIComponent(repo.name)}/readme`, 'application/vnd.github.raw');
        if (res.ok) {
          const cleaned = res.text?.trim() ? cleanReadme(res.text) : '';
          if (cleaned) {
            state.readmes.set(repo.name, cleaned);
            log(`${repo.name}: README read`);
          } else {
            state.noReadme.add(repo.name); // empty or badge-only README
          }
        } else if (res.status === 404) {
          state.noReadme.add(repo.name);
          log(`${repo.name}: no README`);
        }
        // Rate-limited or failed: leave no record, so the output says "not fetched" rather than "none found".
      })(),
    );
  }

  tasks.push(
    (async () => {
      const toInspect = detailed.slice(0, TREE_REPOS);
      let checked = 0;
      await Promise.all(
        toInspect.map(async (repo) => {
          const branch = encodeURIComponent(repo.default_branch || 'main');
          const res = await client.get(`/repos/${encodeURIComponent(state.login)}/${encodeURIComponent(repo.name)}/git/trees/${branch}?recursive=1`);
          if (res.ok && res.json) {
            state.insights.set(repo.name, analyzeTree(res.json as GhTree));
            checked++;
          }
        }),
      );
      if (checked > 0) log(`Checked ${plural(checked, 'repo')} for tests and CI`);
    })(),
  );

  tasks.push(
    (async () => {
      const res = await client.get(`/users/${encodeURIComponent(state.login)}/events/public?per_page=100`);
      if (res.ok && Array.isArray(res.json)) {
        const languageByRepo = new Map<string, string>();
        for (const r of repos) if (r.language) languageByRepo.set(r.full_name.toLowerCase(), r.language);
        state.activity = summarizeEvents(res.json as GhEvent[], state.login, languageByRepo);
        log(`Read ${plural(state.activity.total, 'recent public event')}`);
      }
    })(),
  );

  if (profileReadmeRepo) {
    tasks.push(
      (async () => {
        const res = await client.get(`/repos/${encodeURIComponent(state.login)}/${encodeURIComponent(profileReadmeRepo.name)}/readme`, 'application/vnd.github.raw');
        if (res.ok && res.text && res.text.trim()) {
          state.profileReadme = cleanReadme(res.text);
          log('Profile README read');
        }
      })(),
    );
  }

  await Promise.all(tasks);

  if (client.rateLimited) {
    state.notes.push('GitHub rate limit was reached part-way, so some READMEs, file checks or activity are missing.');
    log('Rate limit reached, continuing with what was fetched');
  }
  return finish();
}

/* ───────────────────────── output ───────────────────────── */

function buildSource(
  base: { id: string; kind: 'github'; label: string; url: string; kindLabel: string },
  src: string,
  state: State,
  client: GitHubClient,
  error?: string,
): RawSource {
  const now = Date.now();
  const { user, repos } = state;
  const { detailed, ranked } = state;

  const originals = (repos ?? []).filter((r) => !r.fork);
  const forks = (repos ?? []).filter((r) => r.fork);
  const stars = originals.reduce((sum, r) => sum + (r.stargazers_count || 0), 0);
  const langCounts = topCounts(originals.map((r) => r.language).filter((l): l is string => !!l), 8);
  const languages = langCounts.map(([l]) => l);
  const withTests = [...state.insights.values()].filter((i) => i.tests).length;
  const withCI = [...state.insights.values()].filter((i) => i.ci.length > 0).length;
  const withDocker = [...state.insights.values()].filter((i) => i.docker).length;
  const inspected = state.insights.size;

  const out: string[] = [];
  const displayName = user?.name ? `${user.name} (@${state.login})` : `@${state.login}`;
  out.push(`# GitHub: ${displayName}`);

  if (user) {
    out.push('', '## Profile');
    if (user.name) out.push(`- Name: ${user.name}`);
    out.push(`- Account type: ${user.type ?? 'User'}`);
    if (user.bio) out.push(`- Bio: ${user.bio.replace(/\s+/g, ' ').trim()}`);
    if (user.company) out.push(`- Company: ${user.company}`);
    if (user.location) out.push(`- Location: ${user.location}`);
    if (user.blog) out.push(`- Website: ${user.blog}`);
    if (user.twitter_username) out.push(`- X/Twitter: @${user.twitter_username}`);
    out.push(`- Followers: ${fmtInt(user.followers ?? 0)}, following: ${fmtInt(user.following ?? 0)}`);
    const years = Math.max(0, (now - Date.parse(user.created_at)) / (86_400_000 * 365.25));
    out.push(`- On GitHub since ${dateOnly(user.created_at)} (${years < 1 ? 'less than a year' : `about ${Math.round(years)} years`})`);
  }

  if (state.focus) {
    out.push('', `The user linked one repository specifically: ${state.focus.full_name}. It is listed first below.`);
  }

  if (repos) {
    out.push('', '## Totals');
    const total = user?.public_repos ?? repos.length;
    out.push(
      `- Public repositories: ${fmtInt(total)}` +
        (total > repos.length ? ` (analysing the ${repos.length} most recently pushed)` : ''),
    );
    out.push(`- Original repos: ${originals.length}, forks: ${forks.length}`);
    out.push(`- Stars across original repos: ${fmtInt(stars)}`);
    if (langCounts.length) out.push(`- Languages by number of original repos: ${langCounts.map(([l, n]) => `${l} (${n})`).join(', ')}`);
    const withDesc = originals.filter((r) => r.description).length;
    const withTopics = originals.filter((r) => (r.topics?.length ?? 0) > 0).length;
    const withHome = originals.filter((r) => r.homepage && r.homepage.trim()).length;
    out.push(`- Repos with a description: ${withDesc}/${originals.length}, with topics: ${withTopics}, with a live homepage link: ${withHome}`);
    const allTopics = topCounts(originals.flatMap((r) => r.topics ?? []), 12);
    if (allTopics.length) out.push(`- Common topics: ${allTopics.map(([t, n]) => (n > 1 ? `${t} (${n})` : t)).join(', ')}`);
    if (inspected > 0) {
      out.push(
        `- Engineering practice (checked ${plural(inspected, 'top repo')}): tests in ${withTests}, CI in ${withCI}, Docker in ${withDocker}`,
      );
    }
  }

  if (state.profileReadme) {
    out.push('', '## Profile README (the repo named after the user)', quoteBlock(state.profileReadme));
  }

  if (detailed.length) {
    out.push('', `## Top repositories (${detailed.length}, ranked by stars, recency and substance)`);
    detailed.forEach((r, i) => {
      out.push('', `### ${i + 1}. ${r.name}${r.fork ? ' (fork)' : ''}${r.archived ? ' (archived)' : ''}`);
      out.push(`- Description: ${r.description ? r.description.replace(/\s+/g, ' ').trim() : '(none)'}`);
      out.push(
        `- Language: ${r.language ?? 'n/a'}, stars: ${fmtInt(r.stargazers_count)}, forks: ${fmtInt(r.forks_count)}` +
          (r.license?.spdx_id && r.license.spdx_id !== 'NOASSERTION' ? `, license: ${r.license.spdx_id}` : ''),
      );
      if (r.topics?.length) out.push(`- Topics: ${r.topics.join(', ')}`);
      out.push(`- Last push: ${dateOnly(r.pushed_at)} (${ago(r.pushed_at, now)}), created ${dateOnly(r.created_at)}, size ${fmtInt(r.size)} KB`);
      if (r.homepage && r.homepage.trim()) out.push(`- Homepage: ${r.homepage.trim()}`);
      const ins = state.insights.get(r.name);
      if (ins) {
        out.push(
          `- Detected: tests ${ins.tests ? `yes (${plural(ins.testFiles, 'test file')})` : 'no'}, ` +
            `CI ${ins.ci.length ? `yes (${ins.ci.join(' + ')})` : 'no'}, Docker ${ins.docker ? 'yes' : 'no'}, ` +
            `docs ${ins.docs ? 'yes' : 'no'}${ins.manifests.length ? `, manifests: ${ins.manifests.join(', ')}` : ''}` +
            `${ins.truncated ? ' (file tree truncated)' : ''}`,
        );
      }
      const readme = state.readmes.get(r.name);
      if (readme) out.push('- README excerpt:', quoteBlock(readme));
      else if (state.noReadme.has(r.name)) out.push('- README: none found');
      else if (i < README_REPOS) out.push('- README: not fetched (GitHub rate limit or network error); do not treat it as missing');
    });
  }

  const shownNames = new Set(detailed.map((r) => r.name));
  const others = ranked.filter((r) => !shownNames.has(r.name)).slice(0, 14);
  if (others.length) {
    out.push('', '## Other original repositories');
    for (const r of others) {
      out.push(
        `- ${r.name}${r.language ? ` [${r.language}]` : ''}${r.stargazers_count ? `, ${r.stargazers_count} stars` : ''}, pushed ${dateOnly(r.pushed_at)}` +
          (r.description ? `: ${r.description.replace(/\s+/g, ' ').trim().slice(0, 160)}` : ''),
      );
    }
    const remaining = ranked.length - shownNames.size - others.length;
    if (remaining > 0) out.push(`- …and ${remaining} more`);
  }

  if (forks.length) {
    const forkNames = forks.slice(0, 10).map((f) => f.name);
    out.push('', `## Forks (${forks.length})`, forkNames.join(', ') + (forks.length > 10 ? ', …' : ''));
  }

  const a = state.activity;
  if (a && a.total > 0) {
    out.push('', '## Recent public activity');
    out.push(
      `- ${plural(a.total, 'public event')}` +
        (a.spanDays ? ` over about ${plural(a.spanDays, 'day')}` : '') +
        (a.latest ? `, most recent ${dateOnly(a.latest)} (${ago(a.latest, now)})` : ''),
    );
    const parts = [
      `${plural(a.pushes, 'push', 'pushes')}${a.commits !== null ? ` (${plural(a.commits, 'commit')})` : ''}`,
      `${plural(a.prsOpened, 'pull request')} opened${a.externalPrs ? ` (${a.externalPrs} to other people's repos)` : ''}`,
      a.prsMerged ? `${plural(a.prsMerged, 'PR')} merged` : '',
      a.issuesOpened ? `${plural(a.issuesOpened, 'issue')} opened` : '',
      a.reviews ? `${plural(a.reviews, 'PR review')}` : '',
      a.comments ? `${plural(a.comments, 'comment')}` : '',
      a.reposCreated ? `${plural(a.reposCreated, 'new repo')}` : '',
    ].filter(Boolean);
    out.push(`- ${parts.join(', ')}`);
    if (a.activeRepos.length) out.push(`- Most active repos: ${a.activeRepos.map(([n, c]) => `${n} (${c})`).join(', ')}`);
    if (a.pushLanguages.length) out.push(`- Languages of recent pushes: ${a.pushLanguages.map(([l, c]) => `${l} (${c})`).join(', ')}`);
  } else if (repos && !a) {
    out.push('', '## Recent public activity', '- Not available.');
  }

  // Technologies named across descriptions, topics and READMEs.
  const termText = [
    ...originals.flatMap((r) => [r.description ?? '', ...(r.topics ?? [])]),
    ...state.readmes.values(),
    state.profileReadme ?? '',
    user?.bio ?? '',
  ].join('\n');
  const mentioned = countTerms(termText).slice(0, 14);
  if (mentioned.length) {
    out.push('', '## Technologies mentioned across repos', mentioned.map((m) => m.term).join(', '));
  }

  if (state.notes.length || error) {
    out.push('', '## Notes on this data');
    for (const n of state.notes) out.push(`- ${n}`);
    if (error) out.push(`- ${error}`);
  }

  // Nothing was fetched (user not found, rate limited on the first call): no content, just the error.
  const content = user ? out.join('\n').slice(0, 40_000) : '';

  /* quick signals */
  const quickSignals: string[] = [];
  for (const [lang, n] of langCounts.slice(0, 2)) quickSignals.push(`${lang} · ${plural(n, 'repo')}`);
  const langSet = new Set(languages.map((l) => l.toLowerCase()));
  for (const term of findTerms(termText, 12)) {
    if (quickSignals.length >= 5) break;
    if (!langSet.has(term.toLowerCase()) && term !== 'Git' && term !== 'GitHub') quickSignals.push(term);
  }
  if (withCI > 0) quickSignals.push(`CI in ${plural(withCI, 'repo')}`);
  if (withTests > 0) quickSignals.push(`Tests in ${plural(withTests, 'repo')}`);
  if (stars > 0 && quickSignals.length < 8) quickSignals.push(`${fmtInt(stars)} stars`);

  const facts: Record<string, unknown> = {
    username: state.login,
    repos: user?.public_repos ?? repos?.length ?? 0,
    originals: originals.length,
    forks: forks.length,
    stars,
    languages,
    topRepos: detailed.map((r) => r.name),
    withTests,
    withCI,
    inspected,
    followers: user?.followers ?? 0,
    since: user?.created_at,
    apiCalls: client.calls,
    authenticated: client.authenticated,
    ...(state.notes.length ? { partial: true } : {}),
  };

  return {
    ...base,
    src,
    content,
    facts,
    quickSignals: quickSignals.slice(0, 8),
    ...(error ? { error } : {}),
  };
}
