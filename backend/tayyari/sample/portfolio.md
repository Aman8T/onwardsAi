# Riya Kapoor — Frontend engineer who designs in motion

URL: https://riyakapoor.design
Description: Frontend engineer in Bengaluru. Design systems, motion and the last 10% of polish.
Pages crawled: / · /about · /work/checkout-rebuilt · /work/accessibility-audit · /work/kite-ui · /work/motion-kit · /work/saffron-dashboard · /writing

## Home
"I build interfaces that feel physical — springs, not durations." Frontend engineer at Kitebird in Bengaluru.
Selected work (5 case studies), two essays on motion design, and a link to GitHub. Large animated hero with a
spring-driven cursor trail (respects prefers-reduced-motion). No mention of AI or LLM work anywhere on the site.

## About
Frontend engineer, 2.4 years. Previously interned at Saffron Ledger (fintech dashboards) and freelanced for early-stage
startups. "My favourite work sits between design and engineering: design systems, motion, and the last 10% of polish."
Tools listed: React, TypeScript, Framer Motion, Figma, Storybook, Tailwind.

## Case study 1 — "Checkout, rebuilt" (Kitebird, 2025)
Role: lead frontend engineer, with one designer and one PM.
Problem: a three-step checkout with a 4.1s LCP on the payment step and a high drop-off at the address form.
Process: joined 6 usability sessions, mapped drop-off points, prototyped a single-page checkout in Figma and code,
shipped behind a feature flag, ran an A/B test for 3 weeks.
Outcome (stated near the end of the page): +9.4% checkout conversion, LCP 1.9s on the payment step.
Includes motion specs for the cart drawer and address autocomplete, and before/after screen recordings.

## Case study 2 — "An accessibility audit that actually shipped" (Kitebird, 2025)
Audited checkout against WCAG 2.1 AA with the design lead: 37 issues logged, 21 fixed in two sprints
(focus order, form labels, contrast, live-region announcements for cart updates). Describes testing with NVDA and
VoiceOver by hand. Notes that automated checks (axe) are "next on the list" — not yet in CI.

## Case study 3 — "Kite UI: a design system for a four-person team" (Kitebird, 2024–2025)
42 components, tokens generated from Figma variables via the design-tokens pipeline, Storybook docs,
adoption by 3 product squads. Explains the trade-offs of headless primitives vs styled components.
Mentions typing the whole package in strict TypeScript.

## Case study 4 — "motion-kit: designing with springs" (open source)
Why springs beat duration-based easing, the API design of useSpring/usePresence, gesture handoff, and how the
library reached 212 GitHub stars. Embedded interactive demos.

## Case study 5 — "Saffron Ledger dashboard" (internship, 2024)
Cash-flow charts and invoice tables for small-business bookkeeping; virtualised lists for 10,000+ invoices;
a Figma-backed component kit later adopted across the web app. Outcome not quantified.

## Writing
- "Springs, not durations" (2025) — essay version of her ReactFoo meetup talk.
- "Motion budgets: how much animation is too much?" (2024).

## Observations
- Strong visual craft; every case study has motion specs or interactive demos.
- Outcomes are present but usually appear below the fold, after the process narrative.
- Recipe Genie and notes-ai (AI projects) are not featured.
- No contact form; email link only. Site built with Next.js and Framer Motion, deployed on Vercel.
