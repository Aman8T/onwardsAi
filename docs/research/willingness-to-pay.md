# Willingness to pay: an audit for Indian customers

Date: 2026-10-19. Scope: India only (INR, Razorpay). USD prices are out of scope.

Method: desk research of public prices, surveys and filings (sources at the end), checked against what Tayyari has built and what `project-soul.md` refuses to sell. There is no survey of Tayyari's own users yet, so every price below is a reasoned starting point to test, not a measured willingness to pay. Figures marked **(U)** are uncertain: from a third party, seen only in a search snippet, or conflicting between sources.

## The answer in brief

- **Indians pay for career help one moment at a time, not by the year.** Resume writing (₹900–9,450), Naukri's Priority Applicant (about ₹400), foundit add-ons (₹499–2,499) and Topmate sessions (₹250–1,000) are all one-off buys. Naukri's subscriptions are fixed 3–6 month plans that don't auto-renew month by month. This fits the soul's "per posting or by the month".
- **The first payment comes when a person has a posting they want.** 49% of Indian job seekers say they apply more and hear back less (LinkedIn, Jan 2025), and applicants per role have doubled since 2022. What they will pay for is "make this application count". The second moment is a scheduled interview: only 32% feel prepared (apna, 2025).
- **Three modes: Free, Optimal ₹599 a month, Intense ₹1,999 a month** (section 5). Each is a month bought at a time, with renewal as an opt-in.
  - **Free** takes one real posting all the way to the kit each month, so a student who never pays still gets a complete application.
  - **Optimal at ₹599** is well placed. It costs more than ChatGPT Go and Google AI Plus (₹399) and less than Naukri's paid tiers ("from ₹890", U), Unstop Pro (₹999) and LinkedIn Premium (₹1,500+, U). It is about 2% of a fresher's gross monthly pay.
  - **Intense at ₹1,999** is the price of Claude Pro, ChatGPT Plus and roughly LinkedIn Premium Career. It sells only if it does visibly more: the careful model on everything written about the person, daily search and alerts, interview loops, and an own-domain portfolio. Most of that isn't built yet.
  - **Plus a pack of three postings at ₹199, no expiry (decided 2026-10-19).** It keeps the soul's "pay per posting" promise and gives students a way to pay that isn't a month.
- **What people pay for has to be something ChatGPT can't do.** 87% of Indian job seekers already use generative AI on their resumes (Canva, 2025). Recruiters blame AI-polished applications for harder hiring (LinkedIn India, 2026). So the evidence trail is the product: every line points to your own work.
- **Conversion will be low, so the free tier must be cheap.** For subscription apps in India and South-East Asia, the median share of downloads that pay is 0.7% (RevenueCat, 2026). Naukri reports about 2.2–2.6% of monthly actives paying. At today's spend ceilings, a free user can cost more than a payer brings in. Measure the real cost per analysis before opening the free tier wide.

## 1. What is priced today

| Thing | Today | Where |
|---|---|---|
| Free allowance | One posting match a calendar month, with the gap list. Re-checks and resume or cover generation for an existing match never use a credit. | `billing/entitlements.py`, `TAYYARI_BILLING__FREE_MATCHES_PER_MONTH=1` |
| `pack3` | Three posting matches, no expiry. **Price unset.** | `PACK3_INR` |
| `pass30` | Unlimited postings for 30 days, bought once, no renewal. **Price unset.** | `PASS30_INR` |
| Practice the interview | Free for now, 3 a day | `interview_per_day` |
| Search with AI | Free, 8 a day with an account, 3 without | `jobsearch_per_day` |
| Prepare, Discover, chat, portfolio | Free under rate limits | `GuardSettings` |

The pricing page lists Practice, the prep plan and a custom domain under the pass as "soon". Practice and Prepare have since shipped, so the page is out of date.

## 2. Who pays, and how much they have

| Segment | How many / how stuck | Money | Reading |
|---|---|---|---|
| **Final-year students and unplaced graduates** | About 1.07 crore graduate each year (AISHE 2021-22, U). Unemployment among graduate youth was 29.1% in 2022 (ILO/IHD 2024). 56% are rated employable (India Skills Report 2026). At IITs and NITs, 40–50% of EE/ECE students who sat placements were unplaced by April 2024 (Careers360). | No recent pocket-money data. They already buy student plans at ₹69–89 a month (Spotify, YouTube). Micro-courses sell at ₹49–199. | Most need, least cash. Pays in sachets, around placement season. |
| **Freshers (0–2 years)** | 73% of employers intend to hire freshers in H1 2026 (TeamLease). Some IT majors have delayed onboarding into 2027 (U). | Average offer ₹3.5 LPA against ₹3.8 LPA expected (Indeed, 2025); about ₹25–29k a month gross. | Will pay ₹100–200 for a posting that matters, and ₹300–500 in a month of active applying. |
| **Professionals switching** | 72% are actively looking (LinkedIn India, Nov 2025). 52% are actively looking and 96% are open to it (foundit 2026). 82% might move within 12 months (Aon). 84% feel unprepared for AI-led hiring. | Most expect a 21–30% hike on switching, against a 6–10% appraisal (foundit). They already pay Naukri ₹2,000–9,000 for 6 months (U) and LinkedIn Premium about ₹1,500–1,900 a month (U). | The real market by revenue. Pays ₹500–1,000 a month while active, and some will hold a small subscription while passive. |

**Value check.** A ₹10 LPA professional who switches for 25% earns about ₹2.5 lakh more a year, so a month of Optimal (₹599) is 0.24% of that first year. At ₹15 LPA, a month of Intense (₹1,999) is 0.5% of a ₹3.75 lakh raise. A fresher who lands a ₹3.5 LPA job after a month of Optimal paid about 0.17% of the first year's pay.

What limits willingness to pay is not the value of the outcome. It is cash on hand (students), trust after Byju's and the "job guarantee" ads, and the free alternative (ChatGPT, often free for a year through telecom bundles).

## 3. What they already pay (the anchors)

| Price band | What sits there |
|---|---|
| ₹0 | ChatGPT Go free for 12 months (from Nov 2025); Perplexity Pro free through Airtel; Google AI Pro free for 18 months through Jio; apna and Internshala listings; Pramp peer mocks |
| ₹69–149 | Spotify Student ₹69; YouTube Premium Student ₹89 and Individual ₹149; NextCV ₹100 per resume (U) |
| ₹199–499 | Google AI Plus ₹199 for the first 6 months, then ₹399; ChatGPT Go ₹399; Naukri Priority Applicant about ₹396 a month (U); Topmate 30-minute session ₹250–500 |
| ₹890–1,999 | Naukri FastForward "from ₹890" (U); Unstop Pro ₹999 (crossed-out ₹1,499); Resumod ₹999; Indian resume writers ₹899–1,799; foundit Resume Booster ₹1,499–2,499 (U); LinkedIn Premium Career about ₹1,500–1,899 a month (U, conflicting); Claude Pro and ChatGPT Plus about ₹2,000 a month |
| ₹2,000–9,500 | Naukri 6-month plans ₹2,000–9,000 (U); Naukri resume writing ₹3,250 (entry) to ₹9,450 (executive) |
| ₹7,000–40,000 | GeeksforGeeks interview programmes ₹6,999–14,999; Internshala job-assured ₹39,999 with a refund if not hired (U) |
| Lakhs | Scaler about ₹3.5–4.2 lakh; Masai pay-after-placement |

What the anchors show:

- **₹99, ₹199, ₹499 and ₹999 recur.** No study shows them as psychological price points. They are just where most Indian digital products sit.
- **Paid conversion is low everywhere.** Naukri, the market leader, had about 2.2–2.6% of monthly actives paying in FY26, and its job-seeker billings were ₹176 crore, up 19%. 53% of that revenue is now self-serve.
- **Foreign resume tools rely on trial-then-renew traps.** Resume.io charges $2.95 for 7 days, then $29.95 every 4 weeks. India's dark-pattern guidelines name this "subscription trap". Saying plainly "nothing renews" is a real difference, worth putting on the page.

## 4. How Indians pay

- **Conversion (RevenueCat 2026, India+SE Asia by user location):**
  - download-to-paid median 0.7% (top quartile 1.9%), against 2.0% globally;
  - trial-to-paid 15.2%, against 34.2% in North America;
  - year-one revenue per payer $19.32;
  - prices about half of top-tier markets.
- **A paywall at the moment of value converts about five times better.** Hard-paywall trial-to-paid is 10.7%, against 2.1% for freemium (RevenueCat 2026). For Tayyari, the match comes free and the kit you send is what you buy.
- **UPI is the rail.** It carried 85.5% of payment volume in H2 2025 and 24.07 billion transactions in September 2026.
  - AutoPay mandates are growing fast: 808M executions in July 2025, double the year before (U).
  - More than 20M mandates are revoked every month, mostly for low balance (U).
  - RBI's 2026 e-mandate framework: recurring debits up to ₹15,000 need no extra authentication, but a pre-debit notice is due 24 hours ahead.
  - A monthly subscription is therefore possible but fragile. One-time purchases are robust.
- **Regulation is active.** The Dark Patterns Guidelines 2023 name subscription traps and drip pricing.
  - The CCPA fined McAfee ₹1 lakh (May 2026) for a renewal prompt with no neutral decline.
  - It fined PhysicsWallah ₹5 lakh (June 2026) for a pre-ticked ₹10 box.
  - Platform self-audits are due by 31 Dec 2026.
  - What this means for Tayyari: GST-inclusive prices up front, no pre-ticked add-ons, and cancelling as easy as starting.
- **Trust is low.**
  - 93% of Indian job seekers have met fake jobs, and 51% can't tell a real recruiter from a scammer (Indeed, Jul 2026).
  - ASCI bars ads that imply a job guarantee.
  - Tayyari's refusals are worth showing at the moment of payment: no guarantee, no scraping, every line cited.

## 5. The three modes against the data

The plan is three modes: **Free**, **Optimal** (around ₹600 a month) and **Intense** (around ₹2,000 a month). Shown below as ₹599 and ₹1,999, GST included, because Indian digital prices end in 9 (₹399, ₹999, ₹1,899, ₹1,999). That is convention; no study shows it lifts conversion.

| Mode | Price | Who buys it | Priced just below | Priced just above | Share of monthly pay |
|---|---|---|---|---|---|
| **Free** | ₹0 | Everyone, students above all | — | — | — |
| **Optimal** | ₹599 for 30 days | Freshers and switchers in a month of active applying; students for one month in placement season | ChatGPT Go and Google AI Plus ₹399; Naukri Priority Applicant about ₹396 (U) | Naukri FastForward "from ₹890" (U); Unstop Pro ₹999; LinkedIn Premium ₹1,500+ (U) | 2.1% of a ₹3.5 LPA fresher's gross pay (about ₹29k a month); 0.7% at ₹10 LPA |
| **Intense** | ₹1,999 for 30 days | Professionals earning about ₹12 LPA and up, in the month or two of a serious search or with interviews lined up | LinkedIn Premium Career about ₹1,500–1,899 (U) | LinkedIn Premium Business ₹2,999 (U); Naukri mid-level resume writing ₹4,200–6,250 once | 1.6% at ₹15 LPA; 0.5% of the first year's raise if the switch brings 25% |

**Optimal at ₹599 is well placed.** It costs more than the AI plans people already have, and less than every paid job-seeker product they compare it with. For freshers and switchers it is an easy month. Students judge it against ₹69–149 student plans, though, so most will live on Free and buy one month in placement season. That is fine, as long as Free carries a student through a real application (see decision 4 in section 8).

**Intense at ₹1,999 works only if it does visibly more.** It is the price of Claude Pro and ChatGPT Plus (about ₹2,000) and close to LinkedIn Premium Career. A person will put it next to those. "Optimal with higher caps" loses that comparison. Intense needs things the other two can't give:

- the careful model (Sonnet 5.5) on everything that writes about the person: the graph, the kit, the interviewer;
- daily Search with AI and daily alerts for new fits;
- interview loops with voice: several rounds back to back, as a real panel runs;
- a portfolio on their own domain.

Most of these aren't built yet (section 6). Intense also helps Optimal: a ₹1,999 option beside it makes ₹599 look reasonable. No Indian data says how payers split between a middle and a top plan. Assume most payers buy Optimal, treat Intense as both an anchor and a small high-value group, and measure the split.

**When the first payment happens:**

- **A second posting they want.** The free one showed what the kit does. This is the main path into Optimal.
- **A scheduled interview.** Only 32% feel prepared (apna 2025), and mock users report 38% confidence against 24%. Optimal for practice; Intense for interview loops.
- **The season.** Campus placements run August–December and appraisals January–April (foundit 2026: half of professionals job-hunt in appraisal season).

**A month bought at a time, renewal opt-in.** Soul: "pay per posting or by the month", never by the year. A job search is episodic, and many people will buy one or two months. Offer renewal through UPI AutoPay as an opt-in, with RBI's 24-hour pre-debit notice and cancel in one tap. More than 20M mandates are revoked every month for low balance (U), so expect failed renewals and allow a few days' grace.

**A pack beside the modes (decided 2026-10-19).** The soul says "pay per posting or by the month", and `pack3` already exists in code. So a pack of three postings at ₹199, no expiry, sits beside Free, Optimal and Intense. Without it, someone who wants one more posting would have to buy a month. Students, whose anchors are ₹69–149, would be hit hardest. At ₹66 a posting, three packs (₹597) cost about the same as a month of Optimal. Someone applying a lot gets more from Optimal, and the pricing page can say so.

**Why not a 3- or 6-month plan.** Naukri sells them and they would lift order value, but the soul rules out making a student buy a season to find out.

**Why not a match score or a visibility boost.** No "ATS %" (soul). No "priority applicant", because we are not the board and cannot deliver it. No guarantee (ASCI, Byju's).

## 6. The feature map across the three modes

**Built?** says whether the feature exists today. Model cost is the spend reservation in `core/settings.py` at about ₹88 to the dollar: an upper bound, not a measurement. Search with AI (about ₹10 a run) is the one measured cost.

| Feature | Built? | Free | Optimal ₹599 | Intense ₹1,999 | Why |
|---|---|---|---|---|---|
| Resume and links → skill graph, radar, next roles | Yes | Yes | Yes | Built by the careful model | The hook. A better graph is Intense's most visible step up. ≤ ₹35 per analysis on Haiku. |
| Chat about the graph | Yes | Daily cap | Higher cap | Highest cap, careful model | Leads to Prepare and Practice |
| Portfolio page, radar card (R1) | Yes / next | Yes | Yes | Plus own domain (not built) | Every shared page is an ad. Never paywalled. |
| Posting match: requirements, gaps, band, questions, re-check | Yes | 1 posting a month, all the way to the kit | Unlimited, under the daily caps already in place | Unlimited | Soul: "seeing a gap is free" |
| Application kit: tailored resume, cover note, referral note, PDF and DOCX with the checklist | Yes | For the free posting | Unlimited | Unlimited, written by the careful model | The core: what a person sends an employer |
| Prepare: where you stand, the one move, the plan, ticks | Yes | Yes | Yes | Yes | Why people come back. ≤ ₹5. |
| Discover browsing, Compare, Not for me, Your applications | Yes | Yes | Yes | Yes | Never calls a model; the tracker brings people back |
| Search with AI | Yes | 2 runs a month | 10 runs a month | 2 a day | The costliest repeat (≈ ₹10 a run) |
| Practice the interview | Yes | 1 of each kind a month, typed or browser voice | 3 a day, Sarvam voice | Unlimited under fair use, careful interviewer | The interview is the second paying moment |
| Interview loops: several rounds back to back | Not built | — | — | Yes | The Intense difference at the interview moment |
| Alerts for new fits | Not built | — | Weekly | Daily | Ongoing value is what keeps a month renewing |
| Graph refresh from GitHub and deployed work | Partly | On request, once a month | Weekly | Whenever there is new work | Data that compounds (soul) |
| Proof line on the resume: a portfolio link where every line cites | Partly | Yes | Yes | Yes | Answers recruiters' distrust of AI polish. It sells the product, so it stays in every mode. |

What each mode is for:

- **Free:** take one real posting all the way, with nothing missing. Show the graph, the portfolio, the gaps and Prepare. A student who never pays still gets a complete application a month.
- **Optimal:** apply as much as you want. Unlimited postings and kits, Practice with voice, more Search with AI. Most payers stop here.
- **Intense:** the careful model on everything written about you, daily search and alerts, interview loops and your own domain. Not worth ₹1,999 until at least two of the unbuilt items exist. Until then, show it on the pricing page as "soon", where it still works as the anchor.

## 7. Unit economics

**What a payment leaves**, after GST at 18% and Razorpay at about 2% plus GST on the fee (standard rate; check the account's rate):

| Mode | Price | After GST | After Razorpay |
|---|---|---|---|
| Optimal | ₹599 | ₹507.6 | about ₹493 |
| Intense | ₹1,999 | ₹1,694 | about ₹1,647 |

**Optimal stays profitable only under its caps.**
- A typical active month costs up to about ₹210 at the ceilings: 10 postings (₹100), 8 Search with AI runs (₹80) and 10 Practice sessions (₹30, plus speech).
- A heavy month can go past ₹493: 30 postings, 20 searches and 60 Practice sessions come to about ₹680 before speech.
- So keep Search with AI at 10 a month and keep the daily caps.

**Intense's cost is unknown.** The careful model costs several times what Haiku does per call, and nothing has been measured on it. Before promising "unlimited" at ₹1,999, run a handful of real profiles through analysis, kit and interview loops on Sonnet 5.5 and read the per-call cost records.

**The free tier is the risk.**
- One new person who analyses once, opens a portfolio, takes one posting to a kit, runs two Search with AI and one Practice, and chats a little costs up to about ₹80–100 at today's ceilings.
- At 2% conversion and about ₹800 per paying month (mostly Optimal, some Intense), revenue is about ₹16 per new person.
- If real costs are a quarter of the ceilings, that is close to break-even.

Before setting prices:

1. **Measure the real cost** of `analyze`, `match`, `jobsearch` and `interview` over a week of the per-call cost records.
2. **Keep the expensive repeats capped in Free:** Search with AI, re-analysis and Practice voice.
3. **Treat the free tier as acquisition cost.** Compare it with what a paid install costs in India, not with zero.

## 8. Price book and the decisions it needs

| Mode | INR, GST included | In code today | What changes |
|---|---|---|---|
| Free | ₹0 | `FREE_MATCHES_PER_MONTH=1`; the free match already includes the kit | Add Free allowances for Search with AI and Practice (Practice is only rate-limited today) |
| Optimal | ₹599 for 30 days | `pass30`, so set `PASS30_INR=59900` | The pricing page names it Optimal; Practice and Search with AI read the mode, not just the daily limits |
| Intense | ₹1,999 for 30 days | Not there | A new product (`intense30`); a model choice per mode (today the models are one setting for everyone); the unbuilt items in section 6 |
| Pack of three postings | ₹199, no expiry | `pack3`, so `PACK3_INR=19900` | Decided: kept beside the modes. Each credit takes one posting all the way to the kit; Practice for that posting comes with it. |

Today `entitlements.consume` only gates posting matches. Modes that change Practice, Search with AI and the model need an entitlements change, and the pricing page needs a new canvas (Free / Optimal / Intense) before code.

Decisions:

1. **The per-posting path. Decided 2026-10-19:** a ₹199 pack of three postings stays beside the three modes.
2. **When Intense goes on sale.** Recommended: show it now as "soon", and sell it once at least two of its unbuilt items exist and its cost on Sonnet 5.5 has been measured.
3. **Renewal.** A month bought at a time, with UPI AutoPay renewal as an opt-in. The roadmap has `pass30` renewal open.
4. **How generous Free is.** One posting a month all the way to the kit (today), or three matches a month with one kit.

## 9. How to find the real numbers

- **Ask in the app.** After a person's second match, offer an optional four-question Van Westendorp survey ("too cheap / a bargain / getting expensive / too expensive for a resume and cover note for one posting"), plus "student / working". About 100 answers per segment are enough for a first curve.
- **Test prices one at a time.** Prices are settings, so run ₹499, ₹599 and ₹699 for Optimal, ₹1,499 and ₹1,999 for Intense once it is on sale, and ₹149 and ₹199 for a pack if one is kept, in time-boxed windows of two weeks each. Don't show two prices to two people at once: Indian users compare notes, and it would look unfair.
- **Track:**
  - match to kit purchase;
  - pass purchase rate in campus and appraisal seasons;
  - a second purchase within 60 days;
  - refund requests;
  - clicks on items marked "soon".
- **Talk to 20 people:** 10 final-year students from tier-2/3 colleges and 10 switchers earning ₹6–20 LPA. Ask what they last paid for in their job search and why.

## 10. What this audit could not verify

- **No survey of how many Indian job seekers pay for career tools, or how much.** Searches found none.
- **No recent data on student pocket money or test-prep spend.** The ₹1,500 figure is from 2011.
- **Official prices unread.** LinkedIn's, Naukri's and Unstop's own price pages could not be opened. Their figures are third-party (U).
- **Naukri's paid share conflicts.** Two calls give two ranges: 1.2→2.2% over 15 months, and 1.3→2.6% over six quarters.
- **Model costs are ceilings, not measurements.** Search with AI (about $0.11 a run) is the only measured one.
- **Fees and tax unchecked.** Razorpay's fee on this account, GST registration, and the exchange rate (₹88 assumed).
- **Not covered:** college or placement-cell buyers. The soul says students are served directly, not through colleges.

## Sources

**Conversion, payments, regulation**
- RevenueCat, State of Subscription Apps 2026: https://www.revenuecat.com/state-of-subscription-apps ; benchmarks: https://www.revenuecat.com/blog/growth/subscription-app-trends-benchmarks-2026 ; education: https://www.revenuecat.com/state-of-subscription-apps-2026-education
- Sensor Tower via TechCrunch (Apr 2026): https://techcrunch.com/2026/04/22/indias-app-market-is-booming-but-global-platforms-are-capturing-most-of-the-gains/
- RBI e-mandate framework 2026 (Medianama): https://www.medianama.com/2026/04/223-rbi-additional-factor-authentication-e-mandates/
- UPI AutoPay revocations (Business Standard, 2025, U): https://www.business-standard.com/amp/finance/news/upi-autopay-revocations-hit-20-mn-monthly-over-low-customer-balances-125090700500_1.html
- UPI share of payment volume: https://www.outlookmoney.com/banking/upi-drives-85-per-cent-of-indias-payment-volumes-rtgs-handles-most-high-value-transfers ; September 2026 volumes: https://startuptalky.com/upi-transactions-september-2026/
- CCPA orders on McAfee and PhysicsWallah (2026): https://www.medianama.com/2026/06/223-consumer-protection-authority-fines-physicswallah-and-mcafee-for-dark-patterns-what-counts-as-consent-now/ ; on the dark-pattern guidelines: https://www.barandbench.com/law-firms/view-point/from-nudge-to-notice-what-indias-dark-pattern-orders-mean-for-digital-business

**Consumer spending and edtech**
- SalarySe Gen Z spend: https://www.outlookmoney.com/news/gen-z-spends-over-70-per-cent-of-monthly-spending-on-everyday-expenses-study
- PhysicsWallah average revenue per user: https://entrackr.com/fintrackr/physicswallah-spent-rs-1426-cr-on-salaries-in-fy25-offline-arpu-stood-at-rs-40405-10059672
- Micro-learning prices: https://india.entrepreneur.com/?p=92236
- YouTube Premium India: https://ppc.land/youtube-premium-india-prices/ ; Spotify India (U): https://www.musicbusinessworldwide.com/spotify-cuts-premium-prices-in-india-by-30-and-scraps-lite-tier-just-six-months-after-launching-it/

**AI subscription prices**
- Claude India: https://www.businesstoday.in/technology/news/story/anthropic-introduces-india-pricing-for-claude-ai-plans-pricing-starts-at-542742-2026-07-14
- ChatGPT Go: https://www.croma.com/unboxed/chatgpt-go-free-subscription-1-year
- Google AI Plus: https://techcrunch.com/2025/12/10/google-launches-sub-5-ai-plus-plan-in-india-to-compete-with-chatgpt-go
- Airtel and Perplexity: https://www.bharti.com/press-release-2024-2025-airtel-partners-with-perplexity-powers-every-single-of-its-360mn-customers-with-perplexity-pro.html

**Career-tool prices**
- LinkedIn Premium (U): https://subskart.in/blog/linkedin-premium-price-india-2026
- Naukri plans (U): https://firstdayai.in/blog/naukri-premium-worth-it-2026/ ; Naukri resume writing: https://1millionresume.com/blog/naukri-resume-services-alternative
- foundit (U): https://www.grabon.in/foundit-coupons/
- Unstop Pro: https://unstop.com/pro
- Topmate: https://topmate.io/
- Rezi: https://www.rezi.ai/pricing ; Zety: https://zety.com/pricing ; Resume.io and Teal (U): https://dupple.com/learn/best-ai-resume-builders
- GeeksforGeeks: https://zoutons.com/news/geeksforgeeks-course-price-ladder-august-2026-picks
- Info Edge Q4 FY26 call: https://alphastreet.com/india/info-edge-india-limited-naukri-q4-2026-earnings-call-transcript/
- Indian portal price comparison (U): https://www.zimyo.com/insights/top-job-portals-in-india/

**Job market**
- ILO/IHD India Employment Report 2024: https://www.drishtiias.com/printpdf/india-employment-report-2024-ilo
- PLFS 2025: https://www.mospi.gov.in/uploads/latestReleases/latest_release_1774607827733_3e8964a9-268b-4cc9-ad65-cfc8a9e32f08_Press_note_AR_PLFS_2025_23032025_V2.1_26032026_final.pdf
- India Skills Report 2026: https://news.careers360.com/india-skills-report-2026-employability-56-35-pc-ai-tools-digital-gig-economy-workforce-global-talent-hub
- IIT and NIT placements 2024: https://news.careers360.com/iit-nit-placements-2024-40-pc-b-tech-electrical-electronics-communication-engineering-jobs-ece-ee-package-trends-branch
- TeamLease fresher outlook: https://www.thepeoplesboard.com/news/most-employers-to-hire-freshers-in-jan-june-2026-teamlease-report/
- Indeed fresher pay 2025: https://www.peoplematters.in/news/recruiting-and-onboarding/hiring-in-india-remains-highbut-mostly-for-freshers-willing-to-settle-for-less-indeed-45358
- foundit appraisal survey 2026: https://www.latestly.com/agency-news/business-news-appraisal-season-half-of-indias-professionals-are-already-job-hunting-foundit-appraisal-survey-finds-7529846.html/amp
- LinkedIn India on applicants per role (2026): https://www.peoplematters.in/news/recruiting-and-onboarding/applicants-per-job-double-in-india-intensifying-job-search-pressure-linkedin-47949 ; on recruiters and AI applications: https://www.storyboard18.com/digital/ai-generated-job-applications-add-to-hiring-challenges-in-india-report-88724.htm ; on switching intent: https://www.storyboard18.com/trending/majority-of-indian-professionals-plan-job-switch-in-2026-but-feel-unprepared-for-ai-led-hiring-87252.htm
- Canva, AI resumes: https://www.storyboard18.com/how-it-works/87-of-indian-job-seekers-turn-to-ai-for-resume-updates-canva-report-53641.htm
- apna Interview Readiness Index 2025: https://news.careers360.com/just-one-third-of-job-seekers-consider-themselves-prepared-for-interviews-report
- Indeed India job scams (Jul 2026): https://www.businesstoday.in/jobs/story/more-than-9-in-10-job-seekers-encounter-fake-job-offers-542006-2026-07-09
