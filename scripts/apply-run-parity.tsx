/**
 * The run screens of one application (/apply/<id>) on the dev fixtures, rendered to static markup (no DOM), plus the
 * pure helpers they are built on:
 *   - which screen a stage shows (`runScreen`), and how the screen's own state moves with the stage (`runStateOn`):
 *     optional questions never hold the person on Questions, and a "form changed" after an approval is always shown
 *   - outcomes: never a retry or a "fill it again" once the click may have happened
 *   - Review: Approve and send is aria-disabled, with its reason announced, until nothing required is left; a review
 *     that was already sent once without a clear answer is never approved again; page-set values are shown
 *   - needs: the save box (off for per-offer and self-ID, never for a signature), on-site and file needs have no input,
 *     a checkbox group takes several answers
 *   - Filling and every gallery screen render
 *
 * Run: npm run check:apply
 */
import { register } from 'node:module';

// Register the CSS stub loader before anything imports a stylesheet.
register('./css-stub.mjs', { parentURL: import.meta.url });

const React = await import('react');
const { renderToStaticMarkup } = await import('react-dom/server');

// a minimal window for modules that read it lazily
(globalThis as any).window ??= { setTimeout, clearTimeout, setInterval, clearInterval, location: { search: '' }, history: { replaceState() {} }, scrollTo() {} };

const scr: any = await import('../src/apply/ApplicationScreen');
const out: any = await import('../src/apply/OutcomeView');
const rev: any = await import('../src/apply/ReviewView');
const grp: any = await import('../src/apply/ReviewGroup');
const need: any = await import('../src/apply/NeedCard');
const fill: any = await import('../src/apply/FillingView');
const qv: any = await import('../src/apply/QuestionsView');
const fx: any = await import('../src/apply/dev/runFixtures');
const gal: any = await import('../src/apply/dev/runGallery');

const failures: string[] = [];
let passed = 0;
function check(name: string, ok: boolean, detail = '') {
  if (ok) passed++;
  else failures.push(`${name}${detail ? `: ${detail}` : ''}`);
}
const ESC: Record<string, string> = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#x27;': "'", '&#39;': "'" };
const textOf = (html: string) => html.replace(/<[^>]*>/g, ' ').replace(/&(?:amp|lt|gt|quot|#x27|#39);/g, (m) => ESC[m] ?? m).replace(/\s+/g, ' ').trim();
const h = React.createElement;
const navigate = () => undefined;
const noop = async () => undefined;
const render = (el: any) => renderToStaticMarkup(el);

/* ───── dispatch ───── */
const fresh = { watched: false, opened: false };
const cases: [string, any, any, string][] = [
  ['queued', fx.RUN_QUEUED, fresh, 'filling'],
  ['filling', fx.RUN_FILLING, fresh, 'filling'],
  ['watched fill finished', fx.RUN_REVIEW, { watched: true, opened: false }, 'filling'],
  ['watched and opened', fx.RUN_REVIEW, { watched: true, opened: true }, 'review'],
  ['questions', fx.RUN_QUESTIONS, fresh, 'questions'],
  ['review', fx.RUN_REVIEW, fresh, 'review'],
  ['review with a country question goes to questions', fx.RUN_REVIEW_NEEDS, fresh, 'questions'],
  ['only a signature left: review', fx.RUN_REVIEW_SIGN, fresh, 'review'],
  ['only an on-site field left: review', fx.RUN_REVIEW_ONSITE, fresh, 'review'],
  ['form changed first shows the outcome', fx.RUN_FORM_CHANGED, fresh, 'outcome'],
  ['form changed, opened: review', fx.RUN_FORM_CHANGED, { watched: false, opened: true }, 'review'],
  ['approved', fx.RUN_APPROVED, fresh, 'outcome'],
  ['submitting', fx.RUN_SUBMITTING, fresh, 'outcome'],
  ['needs_signin', fx.RUN_SIGNIN, fresh, 'outcome'],
  ['check_this_one', fx.RUN_CHECK, fresh, 'outcome'],
  ['sent', fx.RUN_SENT, fresh, 'outcome'],
];
for (const [name, d, st, want] of cases) check(`runScreen ${name}`, scr.runScreen(d, st) === want, `got ${scr.runScreen(d, st)}`);

/* ───── outcomes: never a retry after the click ───── */
const kinds: [any, string][] = [
  [fx.RUN_APPROVED, 'sending'],
  [fx.RUN_SUBMITTING, 'sending'],
  [fx.RUN_SIGNIN, 'signin'],
  [fx.RUN_CHALLENGE, 'challenge'],
  [fx.RUN_CODE, 'code'],
  [fx.RUN_FORM_CHANGED, 'form_changed'],
  [fx.RUN_CHECK, 'check'],
  [fx.RUN_FAILED, 'failed'],
  [fx.RUN_CLOSED, 'closed'],
  [fx.RUN_CANCELLED, 'cancelled'],
  [fx.RUN_SENT, 'sent'],
  [fx.RUN_INTERVIEWING, 'sent'],
];
for (const [d, k] of kinds) check(`outcomeKind ${d.stage}/${d.stopReason ?? ''}`, out.outcomeKind(d) === k, `got ${out.outcomeKind(d)}`);
check('outcomeKind filling is not an outcome', out.outcomeKind(fx.RUN_FILLING) === null);

const outcomeProps = (detail: any) => ({ detail, navigate, onResolve: noop, onAppliedThere: noop, onCancel: noop, onFillAgain: noop, onStage: noop, onReview: () => undefined });
for (const d of [fx.RUN_SUBMITTING, fx.RUN_APPROVED, fx.RUN_CHECK, fx.RUN_SENT, fx.RUN_INTERVIEWING]) {
  const acts = out.outcomeActions(out.outcomeKind(d), d);
  check(`no fill again for ${d.stage}`, !acts.includes('fill_again'), acts.join(','));
  const t = textOf(render(h(out.OutcomeView, outcomeProps(d))));
  check(`no retry words for ${d.stage}`, !/try again|fill it again|send again|resend/i.test(t.replace(/We won't send it again on our own/, '')), t.slice(0, 200));
}
check('submitting offers nothing to press', out.outcomeActions('sending', fx.RUN_SUBMITTING).length === 0);
check('approved can still be stopped', out.outcomeActions('sending', fx.RUN_APPROVED).join() === 'dont_send');
check('failed offers fill again', out.outcomeActions('failed', fx.RUN_FAILED).includes('fill_again'));

const checkText = textOf(render(h(out.OutcomeView, outcomeProps(fx.RUN_CHECK))));
check('check this one: It went through', checkText.includes('It went through'));
check("check this one: It didn't: refund me", checkText.includes('It didn’t: refund me'));
check('check this one: time in mono', /Sent at <span class="numeral">/.test(render(h(out.OutcomeView, outcomeProps(fx.RUN_CHECK)))));
check('check this one: danger chip', render(h(out.OutcomeView, outcomeProps(fx.RUN_CHECK))).includes('ap-tone-bad'));

const signText = textOf(render(h(out.OutcomeView, outcomeProps(fx.RUN_SIGNIN))));
for (const w of ['Open the form', 'Copy my answers', 'I applied there']) check(`signin: ${w}`, signText.includes(w));
const chText = textOf(render(h(out.OutcomeView, outcomeProps(fx.RUN_CHALLENGE))));
for (const w of ['Finish on the employer’s page', 'Copy my answers', 'I applied there', 'Skip this one']) check(`challenge: ${w}`, chText.includes(w));
check('challenge: never passes a check', chText.includes('never try to pass it'));

const failText = textOf(render(h(out.OutcomeView, outcomeProps(fx.RUN_FAILED))));
check('failed: refunded chip', failText.includes('Didn’t go · refunded'));
check('failed: hold released', failText.includes('hold on your credit was released'));
const guardText = textOf(render(h(out.OutcomeView, outcomeProps(fx.RUN_FAILED_GUARD))));
check('guard probe: says nothing was filled', guardText.includes('nothing was filled'));

const sentHtml = render(h(out.OutcomeView, { ...outcomeProps(fx.RUN_SENT), confirmation: '/shot.jpg' }));
const sentText = textOf(sentHtml);
for (const w of ['Kestrel Pay has your application', 'Practise the interview', 'Mark a reply', 'exactly as you approved them', 'Asha-Verma-Kestrel-Pay.pdf', 'job-boards.greenhouse.io/kestrelpay/jobs/4021'])
  check(`sent: ${w}`, sentText.includes(w));
check('sent: time in mono', /<dd class="numeral">\d/.test(sentHtml));
check('sent: confirmation image', sentHtml.includes('src="/shot.jpg"'));
check('sent: soft accent chip', sentHtml.includes('ap-tone-ok'));
check('closed: no fill again', !textOf(render(h(out.OutcomeView, outcomeProps(fx.RUN_CLOSED)))).includes('Fill it again'));
check('form changed: Review the new version', textOf(render(h(out.OutcomeView, outcomeProps(fx.RUN_FORM_CHANGED)))).includes('Review the new version'));

check('answersText leaves signatures out', !out.answersText(fx.RUN_SENT).includes('Signature') && out.answersText(fx.RUN_SENT).includes('First name: Asha'));

/* ───── review: approval gated on needs ───── */
check('approveBlock ready', rev.approveBlock(fx.RUN_REVIEW) === null);
check('approveBlock two needs', rev.approveBlock(fx.RUN_REVIEW_NEEDS) === 'Answer the 2 questions marked for you first.', String(rev.approveBlock(fx.RUN_REVIEW_NEEDS)));
check('approveBlock signature', rev.approveBlock(fx.RUN_REVIEW_SIGN) === 'Answer the question marked for you first.');
check('approveBlock on site', /“PAN number” is yours to type on Hollis Health's page/.test(String(rev.approveBlock(fx.RUN_REVIEW_ONSITE))));
check('approveBlock no review', rev.approveBlock({ ...fx.RUN_REVIEW, review: undefined }) !== null);
check('approveBlock optional need does not block', rev.approveBlock({ ...fx.RUN_REVIEW, needs: [{ key: 'x', label: 'x', kind: 'text', required: false, reason: '' }] }) === null);

const reviewProps = (detail: any, over: any = {}) => ({ detail, navigate, queue: fx.RUN_QUEUE, match: fx.RUN_MATCH, onApprove: noop, onAnswer: noop, onCancel: noop, ...over });
function approveButton(html: string) {
  const m = html.match(/<button[^>]*>(?:Approve and send|Approving)<\/button>/);
  return m ? m[0] : '';
}
for (const d of [fx.RUN_REVIEW_SIGN, fx.RUN_REVIEW_ONSITE, fx.RUN_REVIEW_NEEDS]) {
  const html = render(h(rev.ReviewView, reviewProps(d)));
  const btn = approveButton(html);
  check(`approve off for ${d.id}`, btn.includes('aria-disabled="true"'), btn);
  const by = btn.match(/aria-describedby="([^"]+)"/)?.[1];
  const reason = by ? html.match(new RegExp(`id="${by.replace(/[:]/g, '\\:')}"[^>]*>([^<]+)<`))?.[1] : undefined;
  check(`approve reason announced for ${d.id}`, Boolean(reason && reason === rev.approveBlock(d)?.replace(/'/g, '&#x27;')), `${by} -> ${reason}`);
}
const readyHtml = render(h(rev.ReviewView, reviewProps(fx.RUN_REVIEW)));
check('approve on when nothing is left', !approveButton(readyHtml).includes('aria-disabled'), approveButton(readyHtml));
const readyText = textOf(readyHtml);
for (const w of ['Applications · Ready to review', '2 of 5 ready', 'Form · 15 values', 'Where these 15 values come from', 'Attached only when you approve', 'Not now', "Don't apply", 'The match', 'Good match.', 'Emails about this application', 'Written from 2 of your facts · 1 sentence dropped', 'Dropped: “I have deep expertise in distributed ledgers.”', 'Typed by you, for this form only', 'Locked'])
  check(`review shows: ${w}`, readyText.includes(w), w);
check('review: tablist roving tabindex', (readyHtml.match(/role="tab"/g) ?? []).length === 4 && (readyHtml.match(/tabindex="0"[^>]*role="tab"|role="tab"[^>]*tabindex="0"/g) ?? []).length === 1);
check('review: chosen option among the others', /ar-opt ar-opt-chosen[^>]*>.*?Yes/.test(readyHtml) && readyHtml.includes('(chosen)'));
check('review: numbered citations', (readyHtml.match(/class="ar-cite"/g) ?? []).length === 2);
check('review: onsite offers Open the form', textOf(render(h(rev.ReviewView, reviewProps(fx.RUN_REVIEW_ONSITE)))).includes('Open the form'));
check('review: groups in form order', grp.groupFields(fx.RUN_REVIEW.review.fields).map((g: any) => g.title).join('|') === 'Personal information|Eligibility|Application questions|Voluntary disclosures|Declaration');
check('review: group counts', /Personal information<\/h2><span[^>]*>6</.test(readyHtml));

const summary = grp.sourceSummary(fx.RUN_REVIEW.review.fields, 'tailored');
check('sourceSummary', summary.map((s: any) => s.text).join(', ') === "7 your details, 1 from this match's resume, 1 written from your facts, 1 typed by you, 5 defaults", summary.map((s: any) => s.text).join(', '));
check('sourceSummary total', summary.reduce((n: number, s: any) => n + s.n, 0) === fx.RUN_REVIEW.review.fields.length);

check('readyPosition middle', JSON.stringify(rev.readyPosition(['a', 'b', 'c'], 'b')) === JSON.stringify({ k: 2, n: 3, prev: 'a', next: 'c' }));
check('readyPosition absent', rev.readyPosition(['a'], 'z') === null);
check('readyPosition first has no prev', rev.readyPosition(['a', 'b'], 'a').prev === undefined);

// arrow keys are ignored inside fields, selects, tabs and radio groups
const el = (match: boolean, editable = false) => ({ isContentEditable: editable, closest: () => (match ? {} : null) });
check('isTypingTarget inside a field', rev.isTypingTarget(el(true)) === true);
check('isTypingTarget elsewhere', rev.isTypingTarget(el(false)) === false);
check('isTypingTarget contenteditable', rev.isTypingTarget(el(false, true)) === true);
check('isTypingTarget null', rev.isTypingTarget(null) === false);
const seen: string[] = [];
rev.isTypingTarget({ isContentEditable: false, closest: (s: string) => (seen.push(s), null) });
for (const sel of ['input', 'textarea', 'select', '[role="tablist"]', '[role="radiogroup"]']) check(`isTypingTarget covers ${sel}`, seen[0]?.includes(sel));

/* ───── needs ───── */
const [c12, ctc, bond, pan] = fx.RUN_QUESTIONS.needs;
const sign = fx.RUN_REVIEW_SIGN.needs[0];
check('save on by default', need.initialSave(c12) === true);
check('save off for per-offer', need.initialSave(bond) === false);
check('save never for a signature', need.initialSave(sign) === false);
check('isQuestion excludes on-site and signature', need.isQuestion(c12) && !need.isQuestion(pan) && !need.isQuestion(sign));
const drafts = { class12: { value: ' 88.4 ', save: true }, currentCtc: { value: '', save: true }, bond: { value: 'No', save: true }, pan: { value: 'ABCDE1234F', save: true }, signature: { value: 'Asha Verma', save: true } };
const batch = need.answerBatch([c12, ctc, bond, pan, sign], drafts);
check('answerBatch', JSON.stringify(batch) === JSON.stringify([{ questionKey: 'class12', value: '88.4', save: true }, { questionKey: 'bond', value: 'No', save: true }, { questionKey: 'signature', value: 'Asha Verma', save: false }]), JSON.stringify(batch));
check('missingRequired', need.missingRequired([c12, ctc, pan], drafts).map((n: any) => n.key).join() === 'current_ctc');
check('saveLabel names the other forms', need.saveLabel(c12) === 'Save it, and use it for Tamarind and Corvid Labs too');
check('saveLabel per offer', need.saveLabel(bond) === 'Remember my answer for every form');

const cardHtml = (n: any) => render(h(need.NeedCard, { need: n, draft: { value: '', save: need.initialSave(n) } }));
check('card: also asked by', textOf(cardHtml(c12)).includes('Also asked by 2 more forms waiting'));
check('card: required by', textOf(cardHtml(c12)).includes('Required by Hollis Health.'));
check('card: unit in mono', /<span class="numeral[^"]*">%<\/span>/.test(cardHtml(c12)));
check('card: save ticked', /<input type="checkbox" checked=""/.test(cardHtml(c12)));
check('card: per-offer save unticked', !/type="checkbox" checked/.test(cardHtml(bond)) && cardHtml(bond).includes('type="checkbox"'));
check('card: on-site has no input', !/<input|<select|<textarea/.test(cardHtml(pan)) && textOf(cardHtml(pan)).includes('You type this one'));
check('card: signature has no save box', !cardHtml(sign).includes('type="checkbox"') && textOf(cardHtml(sign)).includes('never keep it'));

const qText = textOf(render(h(qv.QuestionsView, { detail: fx.RUN_QUESTIONS, navigate, onAnswer: noop, onSkip: noop })));
check('questions: title counted in code', qText.includes("This form asks 3 things we don't know yet."), qText.slice(0, 200));
check('questions: one always yours', qText.includes('And one that’s always yours to type.'));
check('questions: Save and continue + Skip', qText.includes('Save and continue') && qText.includes('Skip this application'));
check('questions: four cards', (render(h(qv.QuestionsView, { detail: fx.RUN_QUESTIONS, navigate, onAnswer: noop, onSkip: noop })).match(/class="panel ar-need/g) ?? []).length === 4);
check('requeuedText', scr.requeuedText(2) === 'Saved. 2 more forms waiting carried on with your answer too.');

/* ───── filling ───── */
const fillingHtml = render(h(fill.FillingView, { detail: fx.RUN_FILLING, navigate, onReady: () => undefined }));
const fillingText = textOf(fillingHtml);
for (const w of ['Filling', 'Nothing has been sent', 'The form, section by section', '6 of 6', '2 of 3 · 1 waits for you', 'Filled so far', '9 of 17 fields', 'Held back. Attached only when you approve.', 'Leave and tell me when it\'s ready'])
  check(`filling: ${w}`, fillingText.includes(w), w);
check('filling: Ready disabled while filling', /<button[^>]*aria-disabled="true"[^>]*>Ready: review it/.test(fillingHtml));
check('filling: live list', /<ul class="ar-fields" aria-live="polite"/.test(fillingHtml));
check('filling: static marks only', !/spinner|animation/i.test(fillingHtml));
const readyFill = render(h(fill.FillingView, { detail: fx.RUN_REVIEW, navigate, onReady: () => undefined }));
check('filling: Ready enabled at waiting_for_you', /<button type="button" class="btn btn-primary">Ready: review it/.test(readyFill), readyFill.match(/<button[^>]*>Ready: review it/)?.[0]);

/* ───── gallery: every run state renders ───── */
for (const [id] of gal.RUN_SCREENS) {
  let html = '';
  try {
    html = render(h(gal.RunGallery, { screen: id, navigate }));
  } catch (e) {
    failures.push(`gallery ${id} threw: ${(e as Error).message}`);
    continue;
  }
  check(`gallery ${id} renders`, textOf(html).length > 80, String(textOf(html).length));
}
check('gallery: unknown screen renders nothing', render(h(gal.RunGallery, { screen: 'nope', navigate })) === '');
const stages = new Set(gal.RUN_SCREENS.map(([id]: [string]) => id));
for (const id of ['run-queued', 'run-filling', 'run-questions', 'run-review', 'run-form-changed', 'run-approved', 'run-submitting', 'run-signin', 'run-challenge', 'run-code', 'run-check', 'run-failed', 'run-failed-guard', 'run-closed', 'run-cancelled', 'run-sent'])
  check(`gallery lists ${id}`, stages.has(id));


/* ───── the screen's state across stages ───── */
{
  const optionalOnly = { ...fx.RUN_REVIEW, needs: [{ key: 'pronouns', label: 'Pronouns', kind: 'text', required: false, reason: 'Optional.', questionKey: 'q:pronouns' }] };
  check('optional questions only: review, not questions', scr.runScreen(optionalOnly, { watched: false, opened: false }) === 'review', scr.runScreen(optionalOnly, { watched: false, opened: false }));
  const fileOnly = { ...fx.RUN_REVIEW, needs: [{ key: 'resume', label: 'Resume', kind: 'file', required: true, reason: 'Add your resume (PDF) in Your details.' }] };
  check('a file need is not a question', scr.runScreen(fileOnly, { watched: false, opened: false }) === 'review');
  check('a file need blocks approval with its reason', /Add it in Your details/.test(String(rev.approveBlock(fileOnly))), String(rev.approveBlock(fileOnly)));

  // watched the fill, went on to Review, approved; the submit pass then found the form changed
  let st = { watched: true, opened: false };
  st = scr.runStateOn(st, 'filling');
  check('sequence: the finished fill waits on Filling', scr.runScreen(fx.RUN_REVIEW, st) === 'filling');
  st = { ...st, opened: true };
  check('sequence: Ready opens Review', scr.runScreen(fx.RUN_REVIEW, st) === 'review');
  st = scr.runStateOn(st, 'approved');
  st = scr.runStateOn(st, 'submitting');
  check('sequence: a later "form changed" is shown first', scr.runScreen(fx.RUN_FORM_CHANGED, st) === 'outcome', scr.runScreen(fx.RUN_FORM_CHANGED, st));
  st = { ...st, opened: true };
  check('sequence: then the new review', scr.runScreen(fx.RUN_FORM_CHANGED, st) === 'review');
  st = scr.runStateOn(st, 'approved');
  check('sequence: a second "form changed" is shown first again', scr.runScreen(fx.RUN_FORM_CHANGED, st) === 'outcome');
  st = scr.runStateOn(st, 'queued');
  check('sequence: answers going in are watched again', st.watched === true && st.opened === false);

  // "It didn't: refund me" left the old review: it is never approved again
  const resolved = { ...fx.RUN_REVIEW, events: [...fx.RUN_REVIEW.events, { at: '2026-11-08T10:00:00Z', kind: 'stage', source: 'user', fromStage: 'check_this_one', toStage: 'waiting_for_you' }] };
  check('not_sent: Approve is blocked', rev.afterUnclearSend(resolved) && rev.approveBlock(resolved) !== null);
  check('not_sent: a plain review is not', !rev.afterUnclearSend(fx.RUN_REVIEW));

  // a skipped field the page set itself is shown with its value
  const preset = { ...fx.RUN_REVIEW, review: { ...fx.RUN_REVIEW.review, skipped: [{ key: 'alerts', label: 'Send me job alerts', kind: 'checkbox', required: false, value: 'checked' }, { key: 'blog', label: 'Blog', kind: 'url', required: false }] } };
  const presetText = textOf(render(h(rev.ReviewView, { detail: preset, navigate, queue: fx.RUN_QUEUE, match: fx.RUN_MATCH, onApprove: noop, onAnswer: noop, onCancel: noop })));
  check('review: page-set value shown', presetText.includes("Set by the employer's form") && presetText.includes('Send me job alerts: checked'));
  check('review: only the empty one is "left empty"', presetText.includes('Left empty: 1 optional field'));

  // needs: a file has no input; a checkbox group takes several answers; self-ID is never saved by default
  const fileCard = render(h(need.NeedCard, { need: fileOnly.needs[0] }));
  check('card: a file need has no input', !/<input|<select|<textarea/.test(fileCard) && textOf(fileCard).includes('Your details'));
  check('a file need is not answerable', !need.answerable(fileOnly.needs[0]) && need.missingRequired(fileOnly.needs, {}).length === 0);
  const group = { key: 'langs', label: 'Languages you use', kind: 'checkboxGroup', required: true, reason: 'x', options: ['Go', 'Python', 'SQL'], questionKey: 'q:langs' };
  const groupCard = render(h(need.NeedCard, { need: group, draft: { value: 'Python; SQL', save: true } }));
  check('card: a checkbox group is checkboxes', (groupCard.match(/<input type="checkbox"[^>]*value="/g) ?? []).length === 3 && !groupCard.includes('type="radio"'), groupCard.match(/<input[^>]*>/g)?.join(' '));
  check('card: a checkbox group shows every pick', (groupCard.match(/<input type="checkbox" checked="" value="/g) ?? []).length === 2, groupCard.match(/<input[^>]*>/g)?.join(' '));
  check('joinPicks keeps the form order', need.joinPicks(['SQL', 'Go'], group.options) === 'Go; SQL');
  check('self-ID is not saved by default', need.initialSave({ key: 'gender', label: 'Gender', kind: 'select', required: false, reason: '', questionKey: 'eeoGender' }) === false);
  check('a self-ID change starts unsaved', need.initialSave(grp.changeNeed({ key: 'gender', label: 'Gender', kind: 'select', required: false, questionKey: 'eeoGender', value: 'Decline to self-identify' })) === false);
}

// ── Z: the attestation flag, a sent time of its own, the employer's page from the check ──
{
  check('an attestation need is read from its flag', need.isAttestation({ key: 'sig_1', questionKey: 'q:sig', attestation: true }) === true);
  check('a need stored before the flag is still read by its key', need.isAttestation({ key: 'x', questionKey: 'signature' }) === true);
  check('a plain need is not an attestation', need.isAttestation({ key: 'x', questionKey: 'noticePeriod' }) === false);
  const apps: any = await import('../src/apply/ApplicationsView');
  const DAY = 86_400_000;
  const now = Date.parse('2026-11-08T10:00:00Z');
  const sent = { id: 's1', stage: 'sent', source: 'match', ats: 'greenhouse', domain: 'x', jobUrl: 'https://x', createdAt: new Date(now - 40 * DAY).toISOString(), updatedAt: new Date(now - 1 * DAY).toISOString(), needsCount: 0, hasScreenshot: false, sentAt: new Date(now - 22 * DAY).toISOString() };
  check('"No reply in 3 weeks" counts from sentAt, not the last change', apps.noReply(sent, now) === true && apps.sentAtOf(sent) === sent.sentAt);
  check('a row with no sentAt falls back to its last change', apps.noReply({ ...sent, sentAt: undefined }, now) === false);
  const start: any = await import('../src/apply/StartView');
  check("the check's applyUrl is the employer's page", start.employerUrlOf({ check: { offered: false, reason: 'unknown_liveness', applyUrl: 'https://job-boards.greenhouse.io/acme/jobs/1' } }) === 'https://job-boards.greenhouse.io/acme/jobs/1');
  check('a known link wins over the check', start.employerUrlOf({ employerUrl: 'https://acme.example/jobs/1', check: { offered: true, applyUrl: 'https://other.example' } }) === 'https://acme.example/jobs/1');
  check('only a web address is linked', start.employerUrlOf({ check: { offered: false, applyUrl: 'javascript:alert(1)' } }) === undefined);
}

// ── A1 review: "Fill it again" for a file-only form, "off" before "sign in", a 402 in plain words ──
{
  const fileOnly = { ...fx.RUN_QUESTIONS, stage: 'waiting_for_you', review: undefined, needs: [{ key: 'resume:tailored', label: 'Finish your resume first', kind: 'file', required: true, reason: 'Your tailored resume still has open questions.', questionKey: 'resume:tailored', perOffer: true }] };
  check('fill again offered when only a file is open', qv.fillAgainOffered(fileOnly) === true);
  check('fill again not offered with a question open', qv.fillAgainOffered(fx.RUN_QUESTIONS) === false);
  check('fill again not offered for an on-site need', qv.fillAgainOffered({ ...fileOnly, needs: [{ ...fileOnly.needs[0], onSite: true }] }) === false);
  const fileText = textOf(render(h(qv.QuestionsView, { detail: fileOnly, navigate, onAnswer: noop, onSkip: noop, onFillAgain: noop })));
  check('questions: a file-only form has Fill it again', fileText.includes('Fill it again') && !fileText.includes('Save and continue'), fileText.slice(0, 300));
  check('questions: no Fill it again with questions open', !textOf(render(h(qv.QuestionsView, { detail: fx.RUN_QUESTIONS, navigate, onAnswer: noop, onSkip: noop, onFillAgain: noop }))).includes('Fill it again'));

  const root: any = await import('../src/apply/ApplyRoot');
  const base = { meError: false, loaded: true, signedIn: true, expired: false };
  check('root: off for a signed-in person while auto-apply is off', root.rootView({ ...base, status: { enabled: false, signedIn: false } }) === 'off');
  check('root: sign in when the session expired', root.rootView({ ...base, expired: true, status: null }) === 'signed_out');
  check('root: sign in for a visitor', root.rootView({ ...base, signedIn: false, status: null }) === 'signed_out');
  check('root: sign in when the server says signed out', root.rootView({ ...base, status: { enabled: true, signedIn: false } }) === 'signed_out');
  check('root: the screen when on and signed in', root.rootView({ ...base, status: { enabled: true, signedIn: true } }) === 'screen');

  const api: any = await import('../src/apply/api');
  const realFetch = globalThis.fetch;
  (globalThis as any).fetch = async () => new Response(JSON.stringify({ error: 'payment_required', reason: 'no_credits' }), { status: 402, headers: { 'content-type': 'application/json' } });
  let said = '';
  try {
    await api.applyApi.retry('ap1');
  } catch (e) {
    said = api.errorText(e);
  } finally {
    (globalThis as any).fetch = realFetch;
  }
  check('a 402 is a sentence, not payment_required', said === api.NO_APPLICATIONS_LEFT && !said.includes('payment_required'), said);
}

if (failures.length) {
  console.error(`apply-run-parity: FAILED (${failures.length}, passed ${passed})`);
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
console.log(`apply-run-parity: OK (${passed} checks)`);
