import { OPERATOR_LINE, SUPPORT_EMAIL } from '../links';
import type { Faq } from '../types';

/**
 * The prices and the date are settings of the server, so they are not written here. These placeholders stay in the file;
 * the server replaces each on every request (backend/tayyari/api/static.py `fill_prices`): a price with its formatted
 * rupee amount, or "Coming soon" when none is set or the plan cannot be bought here; the date with the end of Day One's
 * early price, like "15 Dec 2026". A sentence that holds one must read well either way, so each is the last words of a
 * short labelled fragment ("Price now: ...") and never sits in the middle of a clause.
 */
export const PRICE_DAYONE = '{{price:dayone}}';
export const PRICE_DAYONE_AFTER = '{{price:dayoneafter}}';
export const PRICE_LOWKEY = '{{price:lowkey}}';
export const PRICE_LOCKEDIN = '{{price:lockedin}}';
export const DATE_DAYONE = '{{date:dayone}}';
/** The days within which Day One is refunded in full (`TAYYARI_PLANS__REFUND_DAYS`), as a plain number. */
export const REFUND_DAYS = '{{days:refund}}';

/** The questions of the Pricing shell. Plain text: shown in the no-script body and put in the FAQPage data word for word. */
export const PRICING_FAQ: readonly Faq[] = [
  {
    q: 'Is Leafout free?',
    a: 'Yes, to start. Just Looking is free for as long as you like. Your skill graph, next roles, portfolio, radar card and resumes are never counted. Each month you also get a small allowance of posting matches, searches with AI, interview practice sessions, coach and Ask questions and new analyses, and a few Apply for me applications, counted once. The monthly allowance comes back on the first of the month.',
  },
  {
    q: 'What does it cost?',
    a: `Day One is one payment with no plan limits. Price now: ${PRICE_DAYONE}. The early price ends on: ${DATE_DAYONE}. Price after that: ${PRICE_DAYONE_AFTER}. Lowkey is a monthly plan that is opening soon. Its price: ${PRICE_LOWKEY}. Locked In is a monthly plan that is opening soon. Its price: ${PRICE_LOCKEDIN}.`,
  },
  {
    q: 'Does it renew by itself?',
    a: 'No. Nothing auto-renews. Day One is a single payment, so there is nothing to cancel, and there is no annual plan. Lowkey and Locked In are not on sale yet, and nothing is charged for them.',
  },
  {
    q: 'What do I pay in, and how?',
    a: 'Prices are in rupees, paid through Razorpay by UPI, card or net banking. Leafout never sees your card details.',
  },
  {
    q: 'Can I get a refund?',
    a: `Yes. Day One is refunded in full within ${REFUND_DAYS} days of paying, for any reason. Write to aman@leafout.in, and the refund policy is on leafout.in/refunds.`,
  },
];

/** What a crawler that does not run JavaScript reads on the Pricing shell, inside <noscript>. */
export function PricingSeed() {
  return (
    <>
      <h1>Leafout pricing: free to start, then Day One, once</h1>
      <p>
        Four plans, priced side by side. Start free with Just Looking, or pay once for Day One and stop counting. Nothing renews by itself, there is no annual
        plan, and students pay the same prices as everyone else.
      </p>
      <h2>Just Looking: free</h2>
      <p>For as long as you like, with a small monthly allowance of each counted feature.</p>
      <ul>
        <li>A Leafout account, which the app needs for everything below.</li>
        <li>Your skill graph, your next roles, a portfolio page built from your resume, resumes for each role and the radar card of your skills, never counted.</li>
        <li>Each month: posting matches, searches with AI, interview practice sessions, coach and Ask questions and new analyses. The allowance comes back on the first of the month.</li>
        <li>Apply for me: a few applications, counted once.</li>
      </ul>
      <h2>Lowkey: opening soon</h2>
      <p>A monthly plan with more room than Just Looking. Price: {PRICE_LOWKEY}. It is not on sale yet, and you can ask to be told the day it opens.</p>
      <h2>Day One: one payment</h2>
      <p>
        Everything, with no plan limits and no counting: every posting match, search with AI, practice session, coach question, new analysis and application.
        One payment, never renewed. Price now: {PRICE_DAYONE}. The early price ends on: {DATE_DAYONE}. Price after that: {PRICE_DAYONE_AFTER}.
      </p>
      <h2>Locked In: opening soon</h2>
      <p>A monthly plan for people searching hard. Price: {PRICE_LOCKEDIN}. It is not on sale yet, and you can ask to be told the day it opens.</p>
      <h2>What never costs</h2>
      <p>Seeing a gap. Answering a question about yourself. Re-checking after you did.</p>
      <h2>Paying, refunds and delivery</h2>
      <p>
        Day One is a single payment, and nothing auto-renews. Prices are in rupees, paid through Razorpay by UPI, card or net banking. Leafout never sees your
        card details.
      </p>
      <p>
        Delivery is digital: what you buy is in your account as soon as the payment settles, and nothing is shipped. Day One is refunded in full within {REFUND_DAYS}
        days of paying, for any reason. {OPERATOR_LINE} Write to {SUPPORT_EMAIL}.
      </p>
      <section aria-labelledby="faq-title">
        <h2 id="faq-title">Questions</h2>
        {PRICING_FAQ.map((f, i) => (
          <div className="faq-item" role="group" aria-labelledby={`faq-q-${i + 1}`} key={f.q}>
            <h3 id={`faq-q-${i + 1}`}>{f.q}</h3>
            <p>{f.a}</p>
          </div>
        ))}
      </section>
    </>
  );
}
