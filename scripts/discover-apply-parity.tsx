/**
 * Apply for me on Discover (cards, the role's page, the deck, Compare, Prepare) and the origin hand-off, rendered to
 * static markup (no DOM), plus the pure helpers they are built on:
 *   - applyView: the state of each role in code (off, offered, an earlier application, Workday and forms we don't fill,
 *     signed out, checking, refused, a closed posting, a note)
 *   - refusedApply for each reason a start check says no; the steps while it is checked (no percentages)
 *   - JobCard, Focus, Compare and the Prepare checklist with and without Apply for me
 *   - the origin's runId and apply intent; DiscoverRoot.matchOutcome (wait, open, blocked, paywall, stopped)
 *
 * Run: npm run check:discover
 */
import { register } from 'node:module';

// Register the CSS stub loader before anything imports a stylesheet.
register('./css-stub.mjs', { parentURL: import.meta.url });

const React = await import('react');
const { renderToStaticMarkup } = await import('react-dom/server');

// a minimal window for modules that read it lazily
const store: Record<string, string> = {};
const storage = { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => void (store[k] = v), removeItem: (k: string) => void delete store[k] };
(globalThis as any).window ??= { setTimeout, clearTimeout, setInterval, clearInterval, location: { search: '', pathname: '/discover', origin: 'http://localhost' }, history: { replaceState() {} }, scrollTo() {}, addEventListener() {}, matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }) };
(globalThis as any).sessionStorage ??= storage;
(globalThis as any).localStorage ??= storage;

const parts: any = await import('../src/discover/roles/parts');
const card: any = await import('../src/discover/roles/JobCard');
const focus: any = await import('../src/discover/roles/Focus');
const sections: any = await import('../src/discover/prepare/sections');
const compare: any = await import('../src/discover/compare/CompareView');
const origin: any = await import('../src/discover/origin');
const fx: any = await import('../src/discover/dev/fixtures');

const failures: string[] = [];
let passed = 0;
function check(name: string, ok: boolean, detail = '') {
  if (ok) passed++;
  else failures.push(`${name}${detail ? `: ${detail}` : ''}`);
}
const ESC: Record<string, string> = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#x27;': "'", '&#39;': "'" };
const textOf = (html: string) => html.replace(/<[^>]*>/g, ' ').replace(/&(?:amp|lt|gt|quot|#x27|#39);/g, (m) => ESC[m] ?? m).replace(/\s+/g, ' ').trim();
const h = React.createElement;
const render = (el: any) => renderToStaticMarkup(el);

const STATUS = { enabled: true, signedIn: true, anonymous: false, agents: [] };
const OFF_STATUS = { ...STATUS, enabled: false };
const NO_MAP = new Map();
const WORKDAY = { ...fx.HEADOUT, id: 'hollis-data', employer: 'Hollis Health', applyUrl: 'https://hollis.wd5.myworkdayjobs.com/en-US/careers/job/1', applyHost: 'hollis.wd5.myworkdayjobs.com' };
const OWN_SITE = { ...fx.HEADOUT, id: 'own-site', applyUrl: 'https://careers.example.com/jobs/1', applyHost: 'careers.example.com' };
const app = (stage: string, over: Record<string, unknown> = {}) => ({ id: 'app-1', stage, company: 'Headout', title: 'Frontend Engineer', ats: 'greenhouse', host: 'boards.greenhouse.io', needsCount: 0, createdAt: '2026-11-04T09:00:00Z', updatedAt: '2026-11-04T09:00:00Z', listingId: fx.HEADOUT.id, ...over });
const base = { status: STATUS, signedIn: true, byListing: NO_MAP, flows: NO_MAP };

/* ───────── applyView: the state in code ───────── */

check('off: nothing apply-related', parts.applyView(fx.HEADOUT, { ...base, status: OFF_STATUS }) === undefined);
check('status unknown: nothing', parts.applyView(fx.HEADOUT, { ...base, status: null }) === undefined);
check('greenhouse, signed in: offered', parts.applyView(fx.HEADOUT, base)?.apply.kind === 'offer');
check('lever: offered', parts.applyView(fx.DRIVETRAIN, base)?.apply.kind === 'offer');
check('signed out: signed_out', parts.applyView(fx.HEADOUT, { ...base, signedIn: false })?.apply.kind === 'signed_out');
check('status says signed out: signed_out', parts.applyView(fx.HEADOUT, { ...base, status: { ...STATUS, signedIn: false } })?.apply.kind === 'signed_out');
const wd = parts.applyView(WORKDAY, base);
check('workday: own account with the reason', wd?.apply.kind === 'own_account' && /own account/.test((wd.apply as any).reason));
check('workday, signed out: still the reason', parts.applyView(WORKDAY, { ...base, signedIn: false })?.apply.kind === 'own_account');
const own = parts.applyView(OWN_SITE, base);
check('a form we do not fill: own account, names the host', own?.apply.kind === 'own_account' && (own.apply as any).reason.includes('careers.example.com'));
const sent = parts.applyView(fx.HEADOUT, { ...base, byListing: new Map([[fx.HEADOUT.id, app('sent')]]) });
check('sent earlier: applied, with the date', sent?.apply.kind === 'applied' && (sent.apply as any).id === 'app-1' && /^Sent /.test((sent.apply as any).label), JSON.stringify(sent));
const waiting = parts.applyView(fx.HEADOUT, { ...base, byListing: new Map([[fx.HEADOUT.id, app('waiting_for_you')]]) });
check('waiting earlier: applied, in words', waiting?.apply.kind === 'applied' && (waiting.apply as any).label === 'Waiting for you');
check('applied wins over signed out', parts.applyView(fx.HEADOUT, { ...base, signedIn: false, byListing: new Map([[fx.HEADOUT.id, app('sent')]]) })?.apply.kind === 'applied');
check('failed earlier: offered again (reopens)', parts.applyView(fx.HEADOUT, { ...base, byListing: new Map([[fx.HEADOUT.id, app('failed')]]) })?.apply.kind === 'offer');
check('cancelled earlier: offered again', parts.applyView(fx.HEADOUT, { ...base, byListing: new Map([[fx.HEADOUT.id, app('cancelled')]]) })?.apply.kind === 'offer');
const progress = { kind: 'checking', phase: 'match', requirements: 18, step: 'Checked 3 of 18 requirements…' };
const checking = parts.applyView(fx.HEADOUT, { ...base, flows: new Map([[fx.HEADOUT.id, progress]]) });
check('checking: the state and the steps', checking?.apply.kind === 'checking' && checking.progress === progress && (checking.apply as any).step === progress.step);
check('checking another role: this one offered', parts.applyView(fx.SONICWALL, { ...base, flows: new Map([[fx.HEADOUT.id, progress]]) })?.apply.kind === 'offer');
const refused = parts.applyView(fx.HEADOUT, { ...base, flows: new Map([[fx.HEADOUT.id, { kind: 'refused', apply: { kind: 'off', reason: 'needs 5 years' } }]]) });
check('refused: stays', refused?.apply.kind === 'off' && (refused.apply as any).reason === 'needs 5 years');
const noted = parts.applyView(fx.HEADOUT, { ...base, flows: new Map([[fx.HEADOUT.id, { kind: 'note', text: 'A posting is already being read.' }]]) });
check('note: offered again, with the sentence', noted?.apply.kind === 'offer' && noted.note === 'A posting is already being read.');

/* ───────── refusedApply: what a check that said no means on a card ───────── */

const chk = (reason: string, over: Record<string, unknown> = {}) => ({ offered: false, reason, note: `note for ${reason}`, ...over });
const closed = parts.refusedApply(chk('closed'), STATUS);
check('closed: off, says so, nothing charged', closed.kind === 'off' && closed.reason === parts.CLOSED_REASON);
const already = parts.refusedApply(chk('already_applied', { applicationId: 'app-9' }), STATUS);
check('already applied: opens that application', already.kind === 'applied' && already.id === 'app-9');
check('own account ATS: own account', parts.refusedApply(chk('own_account_ats'), STATUS).kind === 'own_account');
check('manual only: own account', parts.refusedApply(chk('manual_only'), STATUS).kind === 'own_account');
check('unknown liveness: never closed', parts.refusedApply(chk('unknown_liveness'), STATUS).kind === 'own_account');
const gate = parts.refusedApply(chk('blocked_by_requirement', { gateReasons: ['the posting asks for 5 years'] }), STATUS);
check('hard requirement: off with the gate', gate.kind === 'off' && gate.reason === 'the posting asks for 5 years');
check('no credit: off with the note', parts.refusedApply(chk('no_credit'), STATUS).kind === 'off');
check('works while the status said disabled (it was on when pressed)', parts.refusedApply(chk('closed'), OFF_STATUS).kind === 'off');

/* ───────── the steps ───────── */

const s1 = parts.applySteps({ kind: 'checking', phase: 'check' });
check('check phase: four steps, the first now', s1.length === 4 && s1[0].mood === 'now' && s1.slice(1).every((s: any) => s.mood === 'wait'));
const s2 = parts.applySteps({ kind: 'checking', phase: 'match' });
check('match started: check done, reading now', s2[0].mood === 'done' && s2[1].mood === 'now' && s2[2].mood === 'wait');
const s3 = parts.applySteps(progress);
check('posting read: count in words, evidence now with the match sentence', s3[1].mood === 'done' && s3[1].text === 'Read the posting: 18 requirements.' && s3[2].mood === 'now' && s3[2].text === progress.step);
check('one requirement: singular', parts.applySteps({ kind: 'checking', phase: 'match', requirements: 1 })[1].text === 'Read the posting: 1 requirement.');
check('the last step always waits (the choice)', [s1, s2, s3].every((s: any) => s[3].mood === 'wait'));
check('no percentage in any step', [s1, s2, s3].flat().every((s: any) => !/\d\s*%/.test(s.text)));

/* ───────── the card in each state ───────── */

const ctl = (over: Record<string, unknown> = {}) => ({
  runReady: true,
  prepareHref: '/discover?tab=prepare&run=r&job=j',
  saved: false,
  comparing: false,
  compareFull: false,
  matchBusy: false,
  matchLocked: false,
  onOpen() {},
  onPrepare() {},
  onSeeMatch() {},
  onToggleSave() {},
  onToggleCompare() {},
  onShare() {},
  onHide() {},
  onUnhide() {},
  onApplyForMe() {},
  onCancelApply() {},
  ...over,
});
const cardOf = (job: any, over: Record<string, unknown> = {}) => render(h(card.JobCard, { job, now: Date.now(), ctl: ctl(over) }));
const idx = (html: string, s: string) => html.indexOf(s);

const offerCard = cardOf(fx.HEADOUT, { apply: { kind: 'offer' } });
check('offer card: Apply for me is a primary button', /<button[^>]*class="[^"]*btn-primary[^"]*"[^>]*>Apply for me/.test(offerCard), offerCard.slice(0, 400));
check('offer card: then the quiet employer link', idx(offerCard, 'Apply for me') < idx(offerCard, 'or apply yourself on boards.greenhouse.io ↗') && /class="btn btn-quiet[^"]*"[^>]*>or apply yourself/.test(offerCard.replace(/aria-label="[^"]*"/g, '').replace(/\s+(href|target|rel)="[^"]*"/g, '')));
check('offer card: the employer link is not primary', !/btn-primary[^>]*>Apply on /.test(offerCard));
check('offer card: Prepare and See how you fit stay', textOf(offerCard).includes('Prepare') && textOf(offerCard).includes('See how you fit'));

const ownCard = cardOf(WORKDAY, { apply: parts.applyView(WORKDAY, base)!.apply });
check('own account card: the employer link is the primary', /btn-primary[^>]*>Apply on hollis\.wd5\.myworkdayjobs\.com/.test(ownCard.replace(/\s+(href|target|rel|aria-label)="[^"]*"/g, '')));
check('own account card: the reason, no Apply for me button', textOf(ownCard).includes('create your own account') && !/>Apply for me</.test(ownCard));

const checkingCard = cardOf(fx.HEADOUT, { apply: { kind: 'checking', step: progress.step }, applyProgress: progress, matchLocked: true });
check('checking card: a status region with the steps', /role="status"/.test(checkingCard) && textOf(checkingCard).includes('Checking the match before applying') && textOf(checkingCard).includes('Read the posting: 18 requirements.'));
check('checking card: Cancel', />Cancel</.test(checkingCard));
check('checking card: no spinner, nothing infinite, no progress bar', !/spin|infinite|<progress|role="progressbar"/i.test(checkingCard));
check('checking card: still the quiet employer link', textOf(checkingCard).includes('or apply yourself on'));
check('checking card: See how you fit locked', /aria-disabled="true"[^>]*>See how you fit/.test(checkingCard));

const appliedCard = cardOf(fx.HEADOUT, { apply: { kind: 'applied', id: 'app-1', label: 'Sent 4 Nov' } });
check('applied card: opens the application', /href="\/apply\/app-1"/.test(appliedCard) && textOf(appliedCard).includes('Sent 4 Nov · open the application'));
check('applied card: no employer link (the application is the way on)', !textOf(appliedCard).includes('apply yourself') && !/greenhouse\.io/.test(appliedCard));

const offCard = cardOf(fx.HEADOUT, { apply: { kind: 'off', reason: 'the posting asks for 5 years' } });
check('off card: shown off with the reason', /aria-disabled="true"[^>]*>Apply for me/.test(offCard) && textOf(offCard).includes('Off: the posting asks for 5 years') && textOf(offCard).includes('or apply yourself on'));

const outCard = cardOf(fx.HEADOUT, { apply: { kind: 'signed_out' } });
check('signed out card: Apply for me (opens sign-in)', />Apply for me</.test(outCard));

const plainCard = cardOf(fx.HEADOUT);
check('auto-apply off: the employer link as before', /btn-primary[^>]*>Apply on boards\.greenhouse\.io/.test(plainCard.replace(/\s+(href|target|rel|aria-label)="[^"]*"/g, '')) && !/Apply for me/.test(plainCard));

const notedCard = cardOf(fx.HEADOUT, { apply: { kind: 'offer' }, notice: 'A posting is already being read. Try again in a moment.' });
check('the card says the last attempt', textOf(notedCard).includes('A posting is already being read'));

/* ───────── the role's page ───────── */

const panel = render(h(focus.Focus, { job: fx.HEADOUT, now: Date.now(), ctl: ctl({ apply: { kind: 'offer' } }), onClose() {} }));
check('role page: Apply for me opens a choice (aria-expanded, aria-controls)', /aria-controls="[^"]+"[^>]*aria-expanded="false"[^>]*>Apply for me/.test(panel) || /aria-expanded="false"[^>]*aria-controls/.test(panel), panel.slice(panel.indexOf('What next'), panel.indexOf('What next') + 600));
const next = panel.slice(idx(panel, 'What next'));
check('role page: Apply for me before the employer link, before Prepare', idx(next, '>Apply for me') > 0 && idx(next, '>Apply for me') < idx(next, 'or apply yourself') && idx(next, 'or apply yourself') < idx(next, 'Prepare for this role'));
const panelOwn = render(h(focus.Focus, { job: WORKDAY, now: Date.now(), ctl: ctl({ apply: parts.applyView(WORKDAY, base)!.apply }), onClose() {} }));
check('role page, own account: employer link primary, with why', textOf(panelOwn).includes('create your own account') && !/>Apply for me</.test(panelOwn));

/* ───────── the choice itself (compact, as the role page shows it once opened) ───────── */

const choiceMod: any = await import('../src/apply/ApplyForMe');
const compact = render(h(choiceMod.ApplyForMeChoice, { id: 'c1', compact: true, company: 'Headout', needsLink: false, firstTime: false, onContinue() {} }));
check('compact choice: "With which resume", two radios, Continue', textOf(compact).includes('With which resume') && (compact.match(/type="radio"/g) ?? []).length === 2 && />Continue</.test(compact));

/* ───────── Compare ───────── */

const applyFor = (job: any) => parts.applyCtlOf(job, 'run-1', { ...base, onApply() {}, onCancel() {} });
const cmp = render(h(compare.CompareView, { jobs: [fx.HEADOUT, WORKDAY], onRemove() {}, onClear() {}, onPrepare() {}, onSeeMatch() {}, onBack() {}, applyOf: applyFor }));
check('compare: Apply for me in the fillable column', />Apply for me</.test(cmp) && textOf(cmp).includes('or apply yourself on boards.greenhouse.io'));
check('compare: the own-account column keeps the employer link and says why', textOf(cmp).includes('Apply on hollis.wd5.myworkdayjobs.com') && textOf(cmp).includes('create your own account'));
const cmpOff = render(h(compare.CompareView, { jobs: [fx.HEADOUT, fx.SONICWALL], onRemove() {}, onClear() {}, onPrepare() {}, onSeeMatch() {}, onBack() {} }));
check('compare without apply: the employer links as before', !/Apply for me/.test(cmpOff) && textOf(cmpOff).includes('Apply on boards.greenhouse.io'));

let pressed: any = 'not yet';
const ctlPressed = parts.applyCtlOf(fx.HEADOUT, 'run-1', { ...base, onApply: (job: any, runId: any, choice: any) => (pressed = { job: job.id, runId, choice }), onCancel() {} });
ctlPressed.onApplyForMe({ resume: 'saved' });
check('applyCtlOf: pressing names the listing, the run and the choice', pressed.job === fx.HEADOUT.id && pressed.runId === 'run-1' && pressed.choice.resume === 'saved');
check('applyCtlOf: no apply, nothing', parts.applyCtlOf(fx.HEADOUT, 'run-1', undefined).apply === undefined);

/* ───────── Prepare's checklist ───────── */

const list = (over: Record<string, unknown>) => render(h(sections.ChecklistSection, { headingId: 'h', job: fx.HEADOUT, ticks: {}, onTick() {}, onApplied() {}, applyHref: fx.HEADOUT.applyUrl, ...over }));
const prepOffer = list({ apply: applyFor(fx.HEADOUT) });
check('prepare: the apply step offers Apply for me, then the quiet link', idx(prepOffer, '>Apply for me') > 0 && idx(prepOffer, '>Apply for me') < idx(prepOffer, 'or apply yourself') && textOf(prepOffer).includes('ticks itself'));
check('prepare: not ticked before it is sent', !/id="[^"]*-apply"[^>]*checked/.test(prepOffer));
const prepSent = list({ apply: parts.applyCtlOf(fx.HEADOUT, 'run-1', { ...base, byListing: new Map([[fx.HEADOUT.id, app('sent')]]), onApply() {}, onCancel() {} }), application: app('sent') });
check('prepare: ticks itself once sent (checked, disabled)', /<input[^>]*id="[^"]*-apply"[^>]*checked=""[^>]*disabled=""/.test(prepSent) || /<input[^>]*checked=""[^>]*disabled=""[^>]*id="[^"]*-apply"/.test(prepSent) || /<input(?=[^>]*-apply")(?=[^>]*checked)(?=[^>]*disabled)[^>]*>/.test(prepSent), prepSent.slice(idx(prepSent, '-apply') - 200, idx(prepSent, '-apply') + 200));
check('prepare: the sent step opens the application', /href="\/apply\/app-1"/.test(prepSent));
const prepOff = list({});
check('prepare without apply: the employer step as before', textOf(prepOff).includes('Apply on boards.greenhouse.io ↗') && !/Apply for me/.test(prepOff));
const prepOwn = list({ job: WORKDAY, applyHref: WORKDAY.applyUrl, apply: applyFor(WORKDAY) });
check('prepare, own account: the employer step with why', textOf(prepOwn).includes('create your own account') && !/>Apply for me</.test(prepOwn));

/* ───────── the origin hand-off ───────── */

const o = { listingId: 'headout-frontend', title: 'Frontend Engineer', employer: 'Headout', applyUrl: fx.HEADOUT.applyUrl, applyHost: 'boards.greenhouse.io', sourceLabel: 'x', discoverHref: '/discover' };
const kept = origin.parseOrigin({ ...o, runId: 'dev-run-20261016', intent: 'apply' });
check('origin keeps runId and the apply intent', kept?.runId === 'dev-run-20261016' && kept?.intent === 'apply');
check('origin drops a bad runId', origin.parseOrigin({ ...o, runId: 'bad id/../x' })?.runId === undefined);
check('origin drops another intent', origin.parseOrigin({ ...o, intent: 'submit' })?.intent === undefined);
check('origin without them: as before', JSON.stringify(Object.keys(origin.parseOrigin(o) ?? {}).sort()) === JSON.stringify(Object.keys(o).sort()));

/* ───────── a posting the start check found closed ───────── */

const closedView = parts.applyView(fx.HEADOUT, { ...base, flows: new Map([[fx.HEADOUT.id, { kind: 'closed' }]]) });
check('closed: nothing offered, the sentence says so', closedView?.closed === true && closedView?.note === parts.CLOSED_NOTE);
const closedCtl = parts.applyCtlOf(fx.HEADOUT, 'run1', { ...base, flows: new Map([[fx.HEADOUT.id, { kind: 'closed' }]]), onApply() {}, onCancel() {} });
check('closed: the controls say closed, with the note', closedCtl.applyClosed === true && closedCtl.note === parts.CLOSED_NOTE);
const closedHtml = render(h(parts.ApplyFor, { job: fx.HEADOUT, ctl: closedCtl, primaryClass: 'p', quietClass: 'q' }));
check('closed: no Apply for me and no employer link', closedHtml === '', closedHtml);

/* ───────── the match behind the card (DiscoverRoot.matchOutcome) ───────── */

const root: any = await import('../src/discover/DiscoverRoot');
const oldResult = { band: 'good', gates: [] };
const newResult = { band: 'good', gates: [] };
const M = (over: Record<string, unknown>) => ({ phase: 'idle', matchId: undefined, result: undefined, blocked: false, posting: undefined, paywall: false, notice: undefined, gates: undefined, ...over });
check('match running: wait', root.matchOutcome(M({ phase: 'reading' }), { seen: true }).kind === 'wait');
check('the old match on screen: wait', root.matchOutcome(M({ phase: 'done', matchId: 'm0', result: oldResult }), { seen: false, before: oldResult }).kind === 'wait');
const opened = root.matchOutcome(M({ phase: 'done', matchId: 'm1', result: newResult }), { seen: true, before: oldResult });
check('done, not blocked: open the match', opened.kind === 'open' && opened.matchId === 'm1');
check('done even if the run was never seen: open', root.matchOutcome(M({ phase: 'done', matchId: 'm1', result: newResult }), { seen: false, before: undefined }).kind === 'open');
const gates = [{ id: 'g1', question: 'At least 5 years of Go?', severity: 3 }];
const blockedOut = root.matchOutcome(
  M({ phase: 'done', matchId: 'm2', blocked: true, posting: { gates }, result: { band: 'stretch', gates: [{ gateId: 'g1', verdict: 'fail', note: 'the posting asks for 5 years; you show 2' }] } }),
  { seen: true },
);
check('done, blocked: the failing gate', blockedOut.kind === 'blocked' && blockedOut.reason === 'the posting asks for 5 years; you show 2');
check('idle before it ran: wait', root.matchOutcome(M({ phase: 'idle' }), { seen: false }).kind === 'wait');
check('idle after running, paywall: paywall', root.matchOutcome(M({ phase: 'idle', paywall: true }), { seen: true }).kind === 'paywall');
const stopped = root.matchOutcome(M({ phase: 'idle', notice: 'We could not read that posting.' }), { seen: true });
check("idle after running: the match's own sentence", stopped.kind === 'stopped' && stopped.note === 'We could not read that posting.');
const quiet = root.matchOutcome(M({ phase: 'idle' }), { seen: true });
check('idle after running, nothing said: a sentence of ours', quiet.kind === 'stopped' && /Nothing was applied/.test(quiet.note));

if (failures.length) {
  console.error(`discover-apply-parity: FAILED (${failures.length}, passed ${passed})`);
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
console.log(`discover-apply-parity: OK (${passed} checks)`);
