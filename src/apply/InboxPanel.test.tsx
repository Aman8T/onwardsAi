/**
 * The application inbox in the web app (InboxPanel.tsx, the inbox calls in api.ts, the paste panel under it), rendered
 * to static markup on the dev fixtures. No DOM, no server: `fetch` is a stub that records what would be sent.
 *
 *   - feature off (or no status): nothing at all
 *   - no address: the exact consent text, an unticked box, "Get my address" disabled until it is ticked
 *   - an address: the address and its copy button, two switches that show the address's state, the recent emails with
 *     their chips and a link only where an application matched; a verification email reads only "Verification code
 *     received" and never carries a code
 *   - chips, notes and copy lines for every status; `?limit=` kept inside the contract's bounds; the request bodies
 *   - the paste panel reads as the fallback once an address is on
 *
 * Run (the CSS stub lets ApplicationsView's stylesheet imports load in Node):
 *   npx tsx --loader ./scripts/css-stub.mjs src/apply/InboxPanel.test.tsx
 */
import { createElement as h } from 'react';
import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { InboxEmail, InboxEmailStatus } from '../../shared/inbox';
import { INBOX_EMAILS_DEFAULT_LIMIT, INBOX_EMAILS_MAX_LIMIT } from '../../shared/inbox';
import { ApplyError, inboxApi, inboxLimit } from './api';
import { INBOX_ADDRESS, INBOX_ADDRESS_OFF, INBOX_EMAILS, INBOX_NO_ADDRESS, INBOX_OFF } from './dev/inboxFixtures';
import { NOW } from './dev/fixtures';
import {
  INBOX_CONSENT_TEXT,
  InboxPanel,
  VERIFICATION_TEXT,
  applicationStageOf,
  applicationsMoved,
  copyNote,
  emailChip,
  emailNote,
  emailsLine,
  inboxActive,
  inboxShown,
  shownSubject,
} from './InboxPanel';
import type { InboxPanelProps } from './InboxPanel';

const failures: string[] = [];
let passed = 0;
function check(name: string, ok: boolean, detail = '') {
  if (ok) passed++;
  else failures.push(`${name}${detail ? `: ${detail}` : ''}`);
}

const ESC: Record<string, string> = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#x27;': "'", '&#39;': "'" };
const textOf = (html: string) =>
  html
    .replace(/<[^>]*>/g, ' ')
    .replace(/&(?:amp|lt|gt|quot|#x27|#39);/g, (m) => ESC[m] ?? m)
    .replace(/\s+/g, ' ')
    .trim();
const render = (el: ReactElement) => renderToStaticMarkup(el);
const noop = async () => undefined;
const panel = (over: Partial<InboxPanelProps>) =>
  render(h(InboxPanel, { status: INBOX_ADDRESS, emails: INBOX_EMAILS, now: NOW, navigate: () => undefined, onCreate: noop, onUpdate: noop, ...over }));

const ALL_STATUSES: InboxEmailStatus[] = ['pending', 'processed', 'unmatched', 'verification', 'quarantined', 'ignored', 'capped', 'failed'];
const mail = (over: Partial<InboxEmail>): InboxEmail => ({
  id: 'x',
  receivedAt: new Date(NOW - 60_000).toISOString(),
  sender: 'a@b.example',
  senderDomain: 'b.example',
  subject: 'Hello',
  status: 'processed',
  ...over,
});

/* ───── feature off: nothing ───── */
{
  check('off: renders nothing', panel({ status: INBOX_OFF }) === '');
  check('no status (loading or failed): renders nothing', panel({ status: null }) === '');
  check('off: not shown, not active', !inboxShown(INBOX_OFF) && !inboxActive(INBOX_OFF) && !inboxShown(null));
  check('an address that is off is not active', inboxShown(INBOX_ADDRESS_OFF) && !inboxActive(INBOX_ADDRESS_OFF));
  check('an active address is active', inboxActive(INBOX_ADDRESS));
  check('no address yet is not active', inboxShown(INBOX_NO_ADDRESS) && !inboxActive(INBOX_NO_ADDRESS));
}

/* ───── no address: consent first ───── */
{
  const html = panel({ status: INBOX_NO_ADDRESS });
  const text = textOf(html);
  check('consent: the exact text is shown', text.includes(INBOX_CONSENT_TEXT), text.slice(0, 400));
  check(
    'consent: the text is the one agreed',
    INBOX_CONSENT_TEXT ===
      "Leafout will receive the emails employers send to this address, read them to update your applications, and send you a copy. We keep the sender, the subject and the stage it set. We don't keep the email itself.",
  );
  check('consent: one checkbox, unticked', (html.match(/type="checkbox"/g) ?? []).length === 1 && !/type="checkbox"[^>]*checked/.test(html), html.match(/<input[^>]*>/g)?.join(' '));
  check('consent: the box is inside its label', /<label[^>]*><input type="checkbox"[^>]*\/><span>Leafout will receive/.test(html));
  check('consent: Get my address is a disabled button until ticked', /<button type="button" class="btn btn-primary" disabled="">Get my address<\/button>/.test(html), html.match(/<button[^>]*>[^<]*<\/button>/g)?.join(' '));
  check('consent: no address, no emails list', !text.includes('Recent emails') && !text.includes('@'));
  check('consent: the panel is a labelled section', /<section class="panel ap-panel ap-stack-s" aria-labelledby="[^"]+"/.test(html));
}

/* ───── an address ───── */
{
  const html = panel({});
  const text = textOf(html);
  check('address: shown', text.includes('jordan.k7m4q@apply.leafout.in'));
  check('address: a copy button that names it', html.includes('aria-label="Copy jordan.k7m4q@apply.leafout.in"'));
  const switches = html.match(/<input type="checkbox" role="switch"[^>]*>/g) ?? [];
  check('address: two switches', switches.length === 2, switches.join(' '));
  check('address: both on', switches.every((s) => s.includes('checked=""')), switches.join(' '));
  check('address: the switches are labelled', text.includes('Receiving mail') && text.includes('Send me a copy'));
  check('address: no consent box again', !text.includes(INBOX_CONSENT_TEXT) && !text.includes('Get my address'));
  check('address: every email is listed', (html.match(/<li class="ap-drow">/g) ?? []).length === INBOX_EMAILS.length);
  check('address: the sender domain is shown', text.includes('ostrava.example') && text.includes('greenhouse.io'));
  check('address: verification reads only as received', text.includes(VERIFICATION_TEXT));
  check('address: no code-like run of digits anywhere', !/\b\d{4,8}\b/.test(text.replace(/\d+-minute/g, '')), text.match(/\b\d{4,8}\b/g)?.join(' '));
  const links = html.match(/<a [^>]*href="\/apply\/[^"]+"[^>]*>/g) ?? [];
  check('address: a link only where an application matched', links.length === INBOX_EMAILS.filter((e) => e.applicationId).length, links.join(' '));
  check('address: the link opens that application', html.includes('href="/apply/a_ostrava"') && html.includes('href="/apply/a_marigold"'));
  check('address: the stage chip is the application stage word', text.includes('Interviewing') && text.includes('Not this time'));
  check('address: the quote is shown', text.includes('“We would love to set up a 30-minute call with the hiring manager.”'));
  check('address: an empty subject still reads', text.includes('(no subject)'));
  check('address: a failed copy is said', text.includes('Your copy did not send'));
  check('address: the time is relative to now', text.includes('4 min ago') && text.includes('3 h ago'));
  check('address: on, the paste-free line', text.includes('Auto-apply puts this address on the forms it fills'));

  const off = textOf(panel({ status: INBOX_ADDRESS_OFF }));
  const offSwitches = panel({ status: INBOX_ADDRESS_OFF }).match(/<input type="checkbox" role="switch"[^>]*>/g) ?? [];
  check('off address: says what off means', off.includes('new forms get your own email'));
  check('off address: the first switch is off, copies still on', !offSwitches[0]?.includes('checked') && offSwitches[1]?.includes('checked=""') === true, offSwitches.join(' '));

  check('emails loading: said', textOf(panel({ emails: null })).includes('Loading your emails'));
  check('no emails yet: said, no list', textOf(panel({ emails: [] })).includes('Nothing yet') && !panel({ emails: [] }).includes('<ul'));
  check('emailsLine: nothing to say with emails', emailsLine(INBOX_EMAILS) === undefined);
}

/* ───── chips, notes, copy lines ───── */
{
  for (const status of ALL_STATUSES) {
    const chip = emailChip(mail({ status }));
    check(`chip for ${status} has words`, chip.text.length > 0);
  }
  check('processed interviewing: the stage word, ok tone', emailChip(mail({ stage: 'interviewing' })).text === 'Interviewing' && emailChip(mail({ stage: 'interviewing' })).tone === 'ok');
  check('processed applied: Sent', emailChip(mail({ stage: 'applied' })).text === 'Sent');
  check('processed rejected: Not this time, plain', emailChip(mail({ stage: 'rejected' })).text === 'Not this time' && emailChip(mail({ stage: 'rejected' })).tone === 'plain');
  check('processed none: No change', emailChip(mail({ stage: 'none' })).text === 'No change');
  check('processed without a stage: No change', emailChip(mail({})).text === 'No change');
  check('failed: the bad tone', emailChip(mail({ status: 'failed' })).tone === 'bad');
  check('verification: only received', emailChip(mail({ status: 'verification' })).text === VERIFICATION_TEXT);
  check('verification: the note never shows a code', !/\d/.test(emailNote(mail({ status: 'verification' })) ?? ''));
  check('unmatched: points at the paste panel', (emailNote(mail({ status: 'unmatched' })) ?? '').includes('Paste it below'));
  check('unmatched with a stage: says what it reads as', (emailNote(mail({ status: 'unmatched', stage: 'applied' })) ?? '').startsWith('It reads as Sent'));
  check('processed: the quote in quotes', emailNote(mail({ quote: 'See you Monday.' })) === '“See you Monday.”');
  check('processed without a quote: no note', emailNote(mail({})) === undefined);
  check('ignored and capped say why not read', (emailNote(mail({ status: 'ignored' })) ?? '').includes('off') && (emailNote(mail({ status: 'capped' })) ?? '').includes('too many'));
  check('copy: sent, failed, skipped', copyNote(mail({ copyStatus: 'sent' })) === 'Copy sent' && copyNote(mail({ copyStatus: 'failed' })) === 'Your copy did not send' && copyNote(mail({ copyStatus: 'skipped' })) === undefined && copyNote(mail({})) === undefined);
  check('stage map: none and absent move nothing', applicationStageOf('none') === undefined && applicationStageOf(undefined) === undefined && applicationStageOf('offer') === 'offer');
}

/* ───── a code never shows, even on rows the server did not read ───── */
{
  const codeRows: InboxEmail[] = [
    mail({ id: 'c1', status: 'pending', subject: 'Your Greenhouse code is 482913' }),
    mail({ id: 'c2', status: 'ignored', subject: 'Your verification code: 7XK2P9' }),
    mail({ id: 'c3', status: 'capped', subject: 'Security code 5521 for Ashby' }),
    mail({ id: 'c4', status: 'failed', subject: 'One-time passcode A1B2C3' }),
  ];
  const text = textOf(panel({ emails: codeRows }));
  check('unread rows: no code in the page', !/482913|7XK2P9|5521|A1B2C3/.test(text), text);
  check('unread rows: the code reads as [code]', text.includes('Your Greenhouse code is [code]'));
  check('a subject that is not about codes is left alone', shownSubject(mail({ status: 'pending', subject: 'Interview on the 14th, 2026' })) === 'Interview on the 14th, 2026');
  check('a read row is shown as stored', shownSubject(mail({ status: 'processed', subject: 'Offer 2026' })) === 'Offer 2026');
  check('no subject', shownSubject(mail({ subject: '' })) === '(no subject)');
}

/* ───── the list reload: which reads mean an application moved ───── */
{
  const pending = mail({ id: 'm1', status: 'pending' });
  const done = mail({ id: 'm1', status: 'processed', applicationId: 'a1', stage: 'interviewing' });
  check('first read never counts', !applicationsMoved(null, [done]));
  check('pending then processed counts', applicationsMoved(new Map([['m1', 'pending']]), [done]));
  check('new and already processed counts', applicationsMoved(new Map(), [done]));
  check('processed seen before does not count again', !applicationsMoved(new Map([['m1', 'processed']]), [done]));
  check('still pending does not count', !applicationsMoved(new Map([['m1', 'pending']]), [pending]));
  check('processed without an application does not count', !applicationsMoved(new Map(), [mail({ id: 'm2', status: 'processed' })]));
}

/* ───── a list that could not load is not an empty one ───── */
{
  check('emailsLine: failed with nothing loaded says so', (emailsLine([], true) ?? '').startsWith('Could not load') && (emailsLine(null, true) ?? '').startsWith('Could not load'));
  check('emailsLine: failed with a list keeps the list', emailsLine(INBOX_EMAILS, true) === undefined);
  const text = textOf(panel({ emails: [], emailsFailed: true }));
  check('failed: never says "Nothing yet"', !text.includes('Nothing yet') && text.includes('Could not load your emails'));
}

/* ───── the calls ───── */
{
  check('limit: default', inboxLimit() === INBOX_EMAILS_DEFAULT_LIMIT);
  check('limit: at least one', inboxLimit(0) === 1 && inboxLimit(-5) === 1);
  check('limit: at most the max', inboxLimit(10_000) === INBOX_EMAILS_MAX_LIMIT);
  check('limit: whole numbers', inboxLimit(12.7) === 12);
  check('limit: not a number is the default', inboxLimit(Number.NaN) === INBOX_EMAILS_DEFAULT_LIMIT);

  const sent: { url: string; method: string; body?: string }[] = [];
  let answer: Response = new Response('{}', { status: 200 });
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    sent.push({ url: String(input), method: init?.method ?? 'GET', body: typeof init?.body === 'string' ? init.body : undefined });
    return answer.clone();
  }) as typeof fetch;
  try {
    answer = new Response(JSON.stringify(INBOX_ADDRESS.address), { status: 200, headers: { 'content-type': 'application/json' } });
    await inboxApi.create();
    check('create: POST with consent', sent[0]?.url === '/api/inbox/address' && sent[0].method === 'POST' && sent[0].body === '{"consent":true}', JSON.stringify(sent[0]));
    await inboxApi.update({ active: false });
    check('update: PATCH with only the change', sent[1]?.url === '/api/inbox/address' && sent[1].method === 'PATCH' && sent[1].body === '{"active":false}', JSON.stringify(sent[1]));
    answer = new Response(JSON.stringify({ emails: [] }), { status: 200, headers: { 'content-type': 'application/json' } });
    await inboxApi.emails(500);
    check('emails: the limit is clamped', sent[2]?.url === `/api/inbox/emails?limit=${INBOX_EMAILS_MAX_LIMIT}`, sent[2]?.url);
    await inboxApi.emails();
    check('emails: the default limit', sent[3]?.url === `/api/inbox/emails?limit=${INBOX_EMAILS_DEFAULT_LIMIT}`, sent[3]?.url);
    answer = new Response(JSON.stringify({ enabled: false }), { status: 200, headers: { 'content-type': 'application/json' } });
    const st = await inboxApi.status();
    check('status: GET, read as is', sent[4]?.url === '/api/inbox/status' && st.enabled === false && st.address === undefined);
    answer = new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404, headers: { 'content-type': 'application/json' } });
    let err: unknown;
    try {
      await inboxApi.emails();
    } catch (e) {
      err = e;
    }
    check('off: a 404 is an ApplyError with its status', err instanceof ApplyError && err.status === 404);
  } finally {
    globalThis.fetch = realFetch;
  }
}

/* ───── the paste panel under it ───── */
{
  const { ApplicationsView } = await import('./ApplicationsView');
  const base = { applications: [], navigate: () => undefined, now: NOW, onStage: noop, onEmail: async () => ({ stage: 'none' as const, confidence: 'low' as const, applied: false }) };
  const plain = textOf(render(h(ApplicationsView, base)));
  check('paste: the own-inbox copy without an inbox', plain.includes('Replies come to your own inbox') && !plain.includes('A reply went to your own email?'));
  const withInbox = render(h(ApplicationsView, { ...base, inbox: h('p', { id: 'inbox-slot' }, 'INBOX'), inboxActive: true }));
  const withText = textOf(withInbox);
  check('paste: the fallback copy with an active inbox', withText.includes('A reply went to your own email?') && !withText.includes('Replies come to your own inbox'));
  check('paste: still offered', withText.includes('Paste an email'));
  check('paste: the inbox sits above it', withInbox.indexOf('inbox-slot') >= 0 && withInbox.indexOf('inbox-slot') < withInbox.indexOf('Paste an email'));
}

if (failures.length) {
  console.error(`inbox-panel: FAILED (${failures.length}, passed ${passed})`);
  for (const f of failures) console.error(`  - ${f}`);
  throw new Error(`inbox-panel: ${failures.length} failed`);
}
console.log(`inbox-panel: ok (${passed} checks)`);
