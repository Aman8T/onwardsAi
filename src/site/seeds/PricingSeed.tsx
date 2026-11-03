import { OPERATOR_LINE, SUPPORT_EMAIL } from '../links';
import type { Faq } from '../types';

/**
 * The prices are settings of the server, so they are not written here. These two placeholders stay in the file; the server
 * replaces each with the formatted rupee price of that plan, or with "Coming soon" when no price is set
 * (backend/tayyari/api/static.py). A sentence that holds one must read well either way.
 */
export const PRICE_PACK3 = '{{price:pack3}}';
export const PRICE_PASS30 = '{{price:pass30}}';

/** The questions of the Pricing shell. Plain text: shown in the no-script body and put in the FAQPage data word for word. */
export const PRICING_FAQ: readonly Faq[] = [
  {
    q: 'Is Leafout free?',
    a: 'The skill graph, your next roles and one posting match each calendar month are free, for as long as you like. Practice interviews are free while they are in testing. Paying is for more postings.',
  },
  {
    q: 'What does it cost?',
    a: `A pack of three postings: ${PRICE_PACK3}, with no expiry. A 30-day pass with unlimited postings: ${PRICE_PASS30}. Both are one-time payments, and one posting match each month stays free.`,
  },
  {
    q: 'Does it renew by itself?',
    a: 'No. Nothing auto-renews: the pack and the 30-day pass are one-time payments. There is no subscription to cancel and no annual plan.',
  },
  {
    q: 'What do I pay in, and how?',
    a: 'Prices are in rupees, paid through Razorpay by UPI, card or net banking. A price in dollars, paid by card through Stripe, is not on sale yet. Leafout never sees your card details.',
  },
  {
    q: 'Can I get a refund?',
    a: 'Yes. You get a full refund within 7 days of purchase if no paid posting from it was used. A posting that could not be read gives its credit back automatically. Write to aman@leafout.in, and the refund policy is on leafout.in/refunds.',
  },
];

/** What a crawler that does not run JavaScript reads on the Pricing shell, inside <noscript>. */
export function PricingSeed() {
  return (
    <>
      <h1>Leafout pricing: free to start, then per posting or for 30 days</h1>
      <p>
        Pay per posting, or by the month. Never by the year. Students pay the same prices as everyone else, and nobody has to buy a year up front to find out
        whether it helps.
      </p>
      <h2>Free</h2>
      <ul>
        <li>Your skill graph and your next roles.</li>
        <li>One posting match each calendar month, with the list of gaps.</li>
        <li>A portfolio page built from your resume, with every line cited.</li>
        <li>The radar card of your skills, as a picture for GitHub or LinkedIn.</li>
        <li>Practice interviews, free while they are in testing.</li>
      </ul>
      <h2>A pack of three postings: {PRICE_PACK3}</h2>
      <p>
        Three posting matches, with no expiry. Each one gives you a resume and a cover note built for that posting, as PDF and DOCX checked before download.
        Re-checking after you close a gap is free.
      </p>
      <h2>A 30-day pass: {PRICE_PASS30}</h2>
      <p>Unlimited postings for 30 days from purchase. It does not renew by itself, and there is no annual plan.</p>
      <h2>What never costs</h2>
      <p>Seeing a gap. Answering a question about yourself. Re-checking after you did.</p>
      <h2>Paying, refunds and delivery</h2>
      <p>
        The pack and the 30-day pass are one-time payments, and nothing auto-renews. Prices are in rupees, paid through Razorpay by UPI, card or net banking; a price in dollars, by card through Stripe, is not on sale yet. Leafout never sees your card details.
      </p>
      <p>
        Delivery is digital: what you buy is in your account as soon as the payment settles, and nothing is shipped. You get a full refund within 7 days of
        purchase if no paid posting from it was used, and a posting that could not be read gives its credit back automatically. {OPERATOR_LINE} Write to{' '}
        {SUPPORT_EMAIL}.
      </p>
      <h2>Questions</h2>
      {PRICING_FAQ.map((f) => (
        <div key={f.q}>
          <h3>{f.q}</h3>
          <p>{f.a}</p>
        </div>
      ))}
    </>
  );
}
