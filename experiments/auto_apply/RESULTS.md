# Auto-apply experiment: results

Branch `exp/auto-apply-test`, run 2026-11-05 to 06. 45 runs on 8 real public postings, **zero applications submitted**.
All money is shown in USD and INR at **₹94.48 per USD** (ECB reference rate via api.frankfurter.app, 2026-11-05;
open.er-api.com gave ₹94.62). Browser Use and Anthropic bill in USD; paying from India by card can add a forex markup
(often 2 to 3.5%) and 18% GST where the provider charges it, so the rupee cost actually paid can be up to about 22% higher
than the figures below. That multiplier is an assumption, not measured.

## TL;DR

| Setup | Full success | Required fields filled | Resume attached | Median time | **Cost per application** |
|---|---|---|---|---|---|
| A: Browser Use agent, `bu-ultrafast` | 1/8 | 50/66 | 0/7 | 50 s | $0.0077 (**₹0.72**) |
| A: Browser Use agent, `bu-fast` | 4/8 | 62/66 | 3/7 | 223 s | $0.0265 (**₹2.50**) |
| B: Claude Sonnet 5.5 + BU browser (final harness, v2) | **6/8** | **57/58** | **6/6** | 84 s | $0.0169 (**₹1.60**): ₹0.08 Browser Use + ₹1.52 Anthropic |
| C: replay of a B trace, no LLM | 5/6 | 42/42 | 5/5 | 61 s | $0.0007 (**₹0.06**) |

- **B beat A on quality and on cost.** It succeeded on 6 of 8 forms versus 4 of 8 for A's better preset, attached the resume every time, and was 36% cheaper than `bu-fast` and 2.7x faster.
- **C shows where the cost goes once playbooks exist: about ₹0.06 per application,** roughly 25x cheaper than B. It replayed 5 of 6 recorded forms perfectly; the one failure was a custom site whose "open the form" click did not reproduce.
- **The sample is tiny** (one run per job per setup, 8 jobs). It is enough to see big gaps (resume upload, token use, dropdown handling), not enough for precise rates. See section 8.
- Total spend: Browser Use **$0.3306 (₹31.23)**, Anthropic **$0.2791 (₹26.37)**, together **$0.61 (₹57.60)**. $0.47 (₹44) of Browser Use credit is left.

## 1. Env findings

- The app loads the Anthropic key as **`ANTHROPIC_API_KEY`** (`backend/tayyari/core/settings.py`, `AliasChoices("ANTHROPIC_API_KEY")`). No other name (`ANTHROPIC_KEY`, `CLAUDE_API_KEY`) is used anywhere in code, config, docs or CI.
- `.env` (repo root) holds `ANTHROPIC_API_KEY` (present, `sk-ant-` prefix) and `BROWSER_USE_API_KEY` (present, `bu_` prefix). The names match what the code reads, so no alias was added.
- `.env` is gitignored (`.gitignore` line 5: `.env`, which covers `backend/.env` too); only `backend/.env.example` is tracked. No key value was printed, logged or committed. Every commit's added lines were scanned for key prefixes and CDP or live-view URLs (which are browser credentials). Two saved event files had live-view URLs; they were redacted and the unpushed commit amended, and the branch history was re-checked clean.
- Sonnet model string already used by the repo: **`claude-sonnet-5-5`**, priced in `llm/models.py` at $2 / 1M input and $10 / 1M output (₹189 / ₹945).
- Browser Use project: free tier, $1 one-time credit ($0.994 when we started), 10 concurrent sessions. The free tier only allows the cost-efficient V4 models, so Arm A could not try stronger models such as `claude-sonnet-5`.
- Python 3.13 via the `uv` already on the machine; `browser-use-sdk` 3.12.1 (V4 import), `playwright` 1.64.1, `anthropic` 2.5.0.

## 2. Cost estimate before any run, and what it really cost

Rates (browser-use.com/pricing.md, updated 2026-10-28): cloud browser $0.02 per browser-hour (₹1.89), billed per minute with a one-minute minimum and unused reserved time refunded on stop; direct egress $0.20/GB (we turned off the $5/GB residential proxy); `bu-ultrafast` bills GPT-6 Luna tokens at the priority rate, 2x standard ($0.24 / 1M input, $0.024 cached, $1.20 output).

| Arm | Estimate written before running | Measured |
|---|---|---|
| A (`bu-ultrafast`) | about $0.04 (₹3.78), range $0.02 to $0.10; cap $0.15 a run | $0.0077 (₹0.72). Cheaper because it gave up early (median 50 s) |
| A (`bu-fast`) | not estimated beforehand | $0.0265 (₹2.50), up to $0.0575 (₹5.43) on the longest form |
| B | about $0.056 (₹5.29): Browser Use $0.002 + Anthropic $0.054 | $0.0169 (₹1.60). One or two Claude calls of about 2.5k input / 0.5k output tokens, not the 6k/1.5k assumed |
| C | about $0.0015 (₹0.14) | $0.0007 (₹0.06) |

The pilot (one A and one B run on the GitLab form) came in far under the estimates, so the budget comfortably covered every job on every setup, plus a second preset for Arm A.

## 3. Safety (every run)

- **Nothing could be submitted.** A Manifest V3 extension loaded into every Browser Use browser (`common/guard.py`) blocks every non-GET request (form posts, fetch/XHR, beacons, file uploads) and every CAPTCHA or bot-challenge provider. Arms B and C add a Playwright route guard with the same rules and refuse submit-like clicks in code. Every run probes the guard from the form's page; **42 of 42 probed runs answered `BLOCKED`** (the 3 unprobed runs are the first Arm A pilot whose scan failed, one reconstructed run and one network crash). The only POSTs ever allowed are two named, read-only Ashby GraphQL operations the form needs to render (`ApiJobPosting`, `ApiOrganizationFromHostedJobsPageName`), found by inspecting the page; Ashby's submit and upload operations stay blocked.
- **No CAPTCHA was solved or bypassed.** Browser Use solves CAPTCHAs automatically and a V4 agent run cannot turn that off, so CAPTCHA scripts were never allowed to load; standalone browsers (B, C) also set `solveCaptchas: false`. CAPTCHA scripts were present on 4 forms (both Lever, both Ashby): the guard kept them from loading, and none of them stopped the form from being filled (they normally fire on submit). No login or account wall appeared.
- **Fictional applicant only:** "Jordan Testcase", an example.com email, a 555-01xx phone and a generated one-page PDF marked TEST. Because uploads are blocked, the dummy resume never reached any employer either.
- Every standalone browser was stopped with `PATCH /api/v4/browsers/{id} {"action":"stop"}` in a `finally` block; Arm A's agent browser was stopped after scoring. The account showed 0 active sessions at the end.

## 4. Test set

| Label | Company | ATS | Role |
|---|---|---|---|
| `gh-gitlab` | GitLab | Greenhouse (job-boards) | Backend Engineer, GitLab Orbit |
| `gh-cloudflare` | Cloudflare | Greenhouse (job-boards) | Capacity Engineer |
| `lv-spotify` | Spotify | Lever | Account Executive, Commercial |
| `lv-palantir` | Palantir | Lever | Administrative Business Partner |
| `ab-linear` | Linear | Ashby | Senior / Staff Fullstack Engineer |
| `ab-ramp` | Ramp | Ashby | Mobile Engineer, Android |
| `cu-workable-huggingface` | Hugging Face | custom: Workable | Open-Source ML Engineer |
| `cu-recruitee-bunq` | bunq | custom: Recruitee on careers.bunq.com | iOS Developer |

## 5. How each run was judged

The same scanner (`common/formscan.py`) reads the form's DOM state in the browser after the run, in every frame, and
`score.py` checks it: every required field holds a value, every personal-detail field (name, email, phone, location,
company, website...) holds the profile's value, and the resume is attached. A run is a **full success** only if all three
hold. Answers to custom screening questions are only checked for being non-empty, which is lenient. Arm A was judged on
the browser's DOM, not on its own report, and that mattered: on GitLab its report said the country field was filled while
the DOM showed it empty.

## 6. Results per setup (latest run per job)

| Setup | Jobs | Full success | Required fields filled | Resume attached | Median time | Avg Browser Use | Avg Anthropic | **Avg total per application** |
|---|---|---|---|---|---|---|---|---|
| A: Browser Use agent (bu-fast) | 8 | 4/8 | 62/66 | 3/7 | 223 s | $0.0265 (₹2.50) | $0.0000 (₹0.00) | **$0.0265 (₹2.50)** |
| A: Browser Use agent (bu-ultrafast) | 8 | 1/8 | 50/66 | 0/7 | 50 s | $0.0077 (₹0.72) | $0.0000 (₹0.00) | **$0.0077 (₹0.72)** |
| B: Claude Sonnet 5.5 + BU browser (v1, first batch) | 8 | 2/8 | 33/35 | 4/4 | 93 s | $0.0008 (₹0.07) | $0.0138 (₹1.30) | **$0.0145 (₹1.37)** |
| B: Claude Sonnet 5.5 + BU browser (v2, final) | 8 | 6/8 | 57/58 | 6/6 | 84 s | $0.0009 (₹0.08) | $0.0160 (₹1.52) | **$0.0169 (₹1.60)** |
| C: replay, no LLM | 6 | 5/6 | 42/42 | 5/5 | 61 s | $0.0007 (₹0.06) | $0.0000 (₹0.00) | **$0.0007 (₹0.06)** |

"Resume attached" counts only forms that had a resume field the scanner could see. B v1's low required-field total is
because 4 of its 8 forms never rendered for it (Ashby under the strict guard, and two custom pages behind a button).

### Per job, final setups

| Job | A `bu-ultrafast` | A `bu-fast` | B v2 | C replay |
|---|---|---|---|---|
| gh-gitlab | 4/9, no resume | **success** | **success** | **success** |
| gh-cloudflare | 7/11, no resume | 10/11, no resume | **success** | **success** |
| lv-spotify | 5/5, no resume | 5/5, no resume | **success** | **success** |
| lv-palantir | 10/10, no resume | 10/10, no resume | 9/10 (location typeahead) | not run |
| ab-linear | 9/10, no resume | **success** | **success** | **success** |
| ab-ramp | 6/7, no resume | **success** | **success** | **success** |
| cu-workable-huggingface | 3/8, no resume | 5/8, no resume | no form found | not run |
| cu-recruitee-bunq | **success** (no resume field) | **success** | **success** | form did not open |

### Speed, steps and tokens

- **B**: 1 to 4 Claude calls per application (median 2), 2k to 15k input and 0.4k to 1.2k output tokens per application, 40 to 121 s. Most wall time is our own page waits; the Claude calls themselves took 4 to 6 s each.
- **A `bu-fast`**: 17 to 77 agent steps, **270k to 2.8M input tokens** per application, 125 to 377 s. It costs about the same as B only because GPT-6 Luna is about 17x cheaper per token than Sonnet ($0.12 vs $2 per 1M input at standard rate); it used 100 to 500x more tokens.
- **A `bu-ultrafast`**: 8 to 12 steps, about 57k input tokens, 23 to 60 s. Fast and cheap because it stops early and leaves fields empty.
- **C**: no tokens; 36 to 164 s, almost all of it page loading and our waits.

## 7. What worked and what broke

Worked:
- **B on the three big ATSs.** In v2, with visible buttons and error details fed back, B filled every required field on both Greenhouse forms, one Lever form and both Ashby forms, with the resume, mostly in one or two calls.
- **Replay (C).** A B trace replayed with no model, on a fresh browser, filled the same forms perfectly 5 times out of 6. This is the strongest finding for cost: a playbook brings the price down to about ₹0.06 per application.
- **The guard.** Blocking every non-GET request is simple and held on every run. The price is that forms which load through POST (Ashby) need named read-only exceptions.

Broke, by failure mode:
1. **Arm A cannot reliably attach the resume.** The hosted agent reported "the browser requires the file in its downloads directory" for the workspace file on 0 of 7 forms with `bu-ultrafast` and 4 of 7 with `bu-fast` (it managed on GitLab and both Ashby forms, not on Lever, Cloudflare or Workable). A form without a resume is not an application, so this one issue sinks A's success rate. Uploading the file into the browser's downloads directory first might fix it; the docs did not describe that for V4 runs and we did not test it.
2. **Custom dropdowns (react-select on Greenhouse) and typeaheads (Lever location).** B v1 failed on these until the scanner ignored react-select's hidden twin inputs and picked options from the dropdown's own listbox (Greenhouse also keeps 245 hidden phone-country options in the DOM). Palantir's location typeahead still beat B v2. `bu-ultrafast` mostly left such fields blank while reporting them filled.
3. **Forms behind a button or a cookie banner.** bunq redirects the form link to the posting page with an "Apply now" button; B could not see buttons until v2. Workable defeated B in both versions ("no form"), while Arm A found the form there; the cause is not yet known.
4. **Resume upload happens at attach time.** On Greenhouse (and Ashby) choosing a file starts an upload to the employer straight away; with the guard it stays at 0%. So a production "fill, show the person, then submit on approval" flow cannot attach the resume during the preview without sending it. See PLAN.md.
5. **Replaying "open the form" clicks on custom sites is flaky.** bunq's replay clicked "Apply now" as recorded, but the form fields were not there in time.
6. **Harness and network errors.** One connection reset and one billing-endpoint error crashed the harness twice (not a browser failure); both were retried, and the crashed Arm A run was reconstructed from its saved scan (marked `reconstructed` in `results.jsonl`, cost taken from its session). Production needs retries on every external call.
7. **Agent self-reports overclaim.** Arm A's own summaries listed fields as filled that were empty in the DOM. Production must verify the page, never trust the agent's report.

## 8. Is paying for two AIs (Anthropic + Browser Use) worth it?

Yes, as long as the second AI is used for very little. In Arm B, Browser Use is only the browser ($0.0009, ₹0.08 per
application); the reasoning is one or two small Claude calls on a structured field list (₹1.52). That beat Browser Use's
all-in-one agent on success (6/8 vs 4/8), resume handling and speed, at 36% lower cost. The real win, though, is that
B's actions are **recorded as selectors and values**, which is what makes replay (C, ₹0.06) possible. Arm A's trace is
JavaScript against element indices from its own page snapshots, which cannot be replayed directly. So the setup to build
is "our code + cloud browser + a model only when needed", not "a hosted agent per application".

Not tested, and worth testing next: **Arm B with Haiku 5.5** ($0.10 / $0.50 per 1M tokens, 20x cheaper than Sonnet)
would bring B's model cost from about ₹1.52 to about ₹0.08 if quality holds.

## 9. Limits of this data (read before concluding anything)

- **One run per job per setup, 8 jobs.** The success rates have very wide error bars; 6/8 versus 4/8 is a hint, not proof. Resume upload (0 to 4 of 7 for A vs 6 of 6 for B) and token use (100 to 500x) are large enough to trust; the exact rates are not.
- The harness changed during the experiment (B v1 to v2, the Ashby allowance). Every final-setup number above comes from the final harness; B v1 is shown for honesty.
- The scorer is lenient on custom questions (non-empty counts) and cannot judge answer quality.
- Arm A ran with a single, untuned task prompt and only the free tier's models.
- Wall-clock times include our own fixed waits (15 s for a form to appear, short pauses after actions); they compare setups, not the best possible speed.
- Live employer pages change daily; these results are a snapshot from 2026-11-05 and 06.

## 10. Calls I made without asking (as the brief allowed)

- **Did not use subagents to write the runners.** The guard, scanner and scorer share one data contract, and splitting them across agents would have risked mismatches. Subagents did the two independent jobs: finding the test postings and the Tsenta/JobsEye research.
- **Ticked required acknowledgement checkboxes** on the fake profile, since nothing is ever sent; production must leave those to the person.
- **Allowed two read-only Ashby POST operations by exact name** after inspecting the page, because no Ashby form renders without them.
- **Re-ran the GitLab pilot** after fixing scanner bugs it exposed, and tagged runs `v1`/`v2`.
- **Counted Browser Use cost** as the larger of the API-reported cost and the drop in the real credit balance, so nothing was under-counted.
- **Stopped at $0.33 of the $0.80 Browser Use line** instead of spending the rest on repeats: a second pass would still not make the sample big enough to change the conclusions.

## 11. Spend

- Browser Use, from the real credit balance (includes guard checks and inspections): **$0.3306 (₹31.23)** of the $0.80 stop line (₹75.58).
- Anthropic (Sonnet 5.5, summed from API usage per call): **$0.2791 (₹26.37)** of the $2 cap (₹188.96).
- Total: **$0.6097 (₹57.60)**. With a possible 3.5% forex markup and 18% GST: up to about ₹70. Running ledger: `spend.json`.

## 12. Every run

Generated by `uv run python summarize.py`. Paths are relative to `experiments/auto_apply/`.

| # | Setup | Job | ATS | Success | Required | Wrong | Resume | Steps | Time | Browser Use | Anthropic | Total (₹) | Failure / note | Screenshot |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | B: Claude Sonnet 5.5 + BU browser (v1) | gh-gitlab | greenhouse | no | 4/13 | 1 | None | 8 | 213 s | $0.0015 | $0.0304 | ₹3.01 | 15 action errors | `runs/20261105T175315-B-gh-gitlab/screenshot.jpg` |
| 2 | B: Claude Sonnet 5.5 + BU browser (v1) | gh-gitlab | greenhouse | no | 8/8 | 2 | False | 12 | 103 s | $0.0010 | $0.0101 | ₹1.05 |  | `runs/20261105T180006-B-gh-gitlab/screenshot.jpg` |
| 3 | A: Browser Use agent (bu-ultrafast) | gh-gitlab | greenhouse | no | 0/0 | 0 | None | 0 | 40 s | $0.0060 | $0.0000 | ₹0.57 |  | none |
| 4 | A: Browser Use agent (bu-ultrafast) | gh-gitlab | greenhouse | no | 4/9 | 0 | False | 12 | 48 s | $0.0073 | $0.0000 | ₹0.69 | agent could not attach the resume | `runs/20261105T180603-A-gh-gitlab/screenshot.jpg` |
| 5 | B: Claude Sonnet 5.5 + BU browser (v1) | gh-gitlab | greenhouse | yes | 9/9 | 0 | True | 12 | 166 s | $0.0007 | $0.0100 | ₹1.01 |  | `runs/20261105T180851-B-gh-gitlab/screenshot.jpg` |
| 6 | B: Claude Sonnet 5.5 + BU browser (v1) | gh-cloudflare | greenhouse | no | 10/11 | 0 | True | 11 | 104 s | $0.0008 | $0.0203 | ₹1.99 | 3 action errors | `runs/20261105T181139-B-gh-cloudflare/screenshot.jpg` |
| 7 | B: Claude Sonnet 5.5 + BU browser (v1) | lv-spotify | lever | yes | 5/5 | 0 | True | 10 | 84 s | $0.0008 | $0.0254 | ₹2.47 | 2 action errors | `runs/20261105T181324-B-lv-spotify/screenshot.jpg` |
| 8 | B: Claude Sonnet 5.5 + BU browser (v1) | lv-palantir | lever | no | 9/10 | 0 | True | 22 | 114 s | $0.0014 | $0.0323 | ₹3.18 | 3 action errors | `runs/20261105T181450-B-lv-palantir/screenshot.jpg` |
| 9 | B: Claude Sonnet 5.5 + BU browser (v1) | ab-linear | ashby | no | 0/0 | 0 | None | 0 | 38 s | $0.0004 | $0.0032 | ₹0.34 | no_form | `runs/20261105T181645-B-ab-linear/screenshot.jpg` |
| 10 | B: Claude Sonnet 5.5 + BU browser (v1) | ab-ramp | ashby | no | 0/0 | 0 | None | 0 | 39 s | $0.0005 | $0.0031 | ₹0.34 | no_form | `runs/20261105T181724-B-ab-ramp/screenshot.jpg` |
| 11 | B: Claude Sonnet 5.5 + BU browser (v1) | cu-workable-huggingface | custom | no | 0/0 | 0 | None | 5 | 102 s | $0.0009 | $0.0129 | ₹1.30 | 8 action errors | `runs/20261105T181804-B-cu-workable-huggingface/screenshot.jpg` |
| 12 | B: Claude Sonnet 5.5 + BU browser (v1) | cu-recruitee-bunq | custom | no | 0/0 | 0 | None | 0 | 42 s | $0.0007 | $0.0031 | ₹0.36 | no_form | `runs/20261105T181947-B-cu-recruitee-bunq/screenshot.jpg` |
| 13 | A: Browser Use agent (bu-ultrafast) | gh-cloudflare | greenhouse | no | 7/11 | 0 | False | 16 | 72 s | $0.0093 | $0.0000 | ₹0.88 | agent could not attach the resume | `runs/20261105T182032-A-gh-cloudflare/screenshot.jpg` |
| 14 | A: Browser Use agent (bu-ultrafast) | lv-spotify | lever | no | 5/5 | 0 | False | 9 | 48 s | $0.0078 | $0.0000 | ₹0.73 | agent could not attach the resume | `runs/20261105T182146-A-lv-spotify/screenshot.jpg` |
| 15 | A: Browser Use agent (bu-ultrafast) | lv-palantir | lever | no | 10/10 | 0 | False | 14 | 62 s | $0.0108 | $0.0000 | ₹1.02 | agent could not attach the resume | `runs/20261105T182235-A-lv-palantir/screenshot.jpg` |
| 16 | A: Browser Use agent (bu-ultrafast) | ab-linear | ashby | no | 0/0 | 0 | None | 4 | 33 s | $0.0020 | $0.0000 | ₹0.19 |  | `runs/20261105T182337-A-ab-linear/screenshot.jpg` |
| 17 | A: Browser Use agent (bu-ultrafast) | ab-ramp | ashby | no | 0/0 | 0 | None | 3 | 32 s | $0.0020 | $0.0000 | ₹0.19 |  | `runs/20261105T182411-A-ab-ramp/screenshot.jpg` |
| 18 | A: Browser Use agent (bu-ultrafast) | cu-workable-huggingface | custom | no | 3/8 | 0 | False | 9 | 48 s | $0.0066 | $0.0000 | ₹0.63 | agent could not attach the resume | `runs/20261105T182444-A-cu-workable-huggingface/screenshot.jpg` |
| 19 | A: Browser Use agent (bu-ultrafast) | cu-recruitee-bunq | custom | yes | 6/6 | 0 | None | 10 | 58 s | $0.0078 | $0.0000 | ₹0.74 |  | `runs/20261105T182533-A-cu-recruitee-bunq/screenshot.jpg` |
| 20 | B: Claude Sonnet 5.5 + BU browser (v2) | gh-gitlab | greenhouse | yes | 9/9 | 0 | True | 12 | 104 s | $0.0007 | $0.0105 | ₹1.05 |  | `runs/20261105T183022-B-gh-gitlab/screenshot.jpg` |
| 21 | B: Claude Sonnet 5.5 + BU browser (v2) | gh-cloudflare | greenhouse | yes | 11/11 | 0 | True | 13 | 92 s | $0.0015 | $0.0155 | ₹1.60 |  | `runs/20261105T183207-B-gh-cloudflare/screenshot.jpg` |
| 22 | B: Claude Sonnet 5.5 + BU browser (v2) | lv-spotify | lever | yes | 5/5 | 0 | True | 10 | 64 s | $0.0004 | $0.0157 | ₹1.53 |  | `runs/20261105T183340-B-lv-spotify/screenshot.jpg` |
| 23 | B: Claude Sonnet 5.5 + BU browser (v2) | lv-palantir | lever | no | 9/10 | 0 | True | 24 | 121 s | $0.0008 | $0.0414 | ₹3.99 | 2 action errors | `runs/20261105T183445-B-lv-palantir/screenshot.jpg` |
| 24 | B: Claude Sonnet 5.5 + BU browser (v2) | ab-linear | ashby | yes | 10/10 | 0 | True | 10 | 43 s | $0.0010 | $0.0106 | ₹1.10 |  | `runs/20261105T183648-B-ab-linear/screenshot.jpg` |
| 25 | B: Claude Sonnet 5.5 + BU browser (v2) | ab-ramp | ashby | yes | 7/7 | 0 | True | 7 | 40 s | $0.0003 | $0.0087 | ₹0.85 |  | `runs/20261105T183732-B-ab-ramp/screenshot.jpg` |
| 26 | B: Claude Sonnet 5.5 + BU browser (v2) | cu-workable-huggingface | custom | no | 0/0 | 0 | None | 6 | 106 s | $0.0015 | $0.0143 | ₹1.49 | no_form | `runs/20261105T183814-B-cu-workable-huggingface/screenshot.jpg` |
| 27 | B: Claude Sonnet 5.5 + BU browser (v2) | cu-recruitee-bunq | custom | yes | 6/6 | 0 | None | 11 | 76 s | $0.0007 | $0.0116 | ₹1.16 |  | `runs/20261105T184001-B-cu-recruitee-bunq/screenshot.jpg` |
| 28 | A: Browser Use agent (bu-ultrafast) | ab-linear | ashby | no | 9/10 | 0 | False | 10 | 53 s | $0.0070 | $0.0000 | ₹0.66 | agent could not attach the resume | `runs/20261105T184120-A-ab-linear/screenshot.jpg` |
| 29 | A: Browser Use agent (bu-ultrafast) | ab-ramp | ashby | no | 6/7 | 0 | False | 7 | 43 s | $0.0046 | $0.0000 | ₹0.43 | agent could not attach the resume | `runs/20261105T184214-A-ab-ramp/screenshot.jpg` |
| 30 | A: Browser Use agent (bu-fast) | gh-gitlab | greenhouse | yes | 9/9 | 0 | True | 77 | 377 s | $0.0575 | $0.0000 | ₹5.44 |  | `runs/20261105T184300-A-gh-gitlab/screenshot.jpg` |
| 31 | A: Browser Use agent (bu-fast) | gh-cloudflare | greenhouse | no | 10/11 | 0 | False | 51 | 239 s | $0.0312 | $0.0000 | ₹2.95 | agent could not attach the resume | `runs/20261105T184919-A-gh-cloudflare/screenshot.jpg` |
| 32 | A: Browser Use agent (bu-fast) | lv-spotify | lever | no | 5/5 | 0 | False | 52 | 256 s | $0.0340 | $0.0000 | ₹3.21 | agent could not attach the resume | `runs/20261105T185319-A-lv-spotify/screenshot.jpg` |
| 33 | A: Browser Use agent (bu-fast) | lv-palantir | lever | no | 10/10 | 0 | False | 35 | 160 s | $0.0188 | $0.0000 | ₹1.78 | agent could not attach the resume | `runs/20261105T185736-A-lv-palantir/screenshot.jpg` |
| 34 | A: Browser Use agent (bu-fast) | ab-linear | ashby | yes | 10/10 | 0 | True | 37 | 237 s | $0.0180 | $0.0000 | ₹1.70 |  | `runs/20261105T190311-A-ab-linear/screenshot.jpg` |
| 35 | A: Browser Use agent (bu-fast) | ab-linear | ashby | yes | 10/10 | 0 | True | 23 | 0 s | $0.0102 | $0.0000 | ₹0.97 | harness crashed after the run (result not written); reconstructed from | `runs/20261105T190017-A-ab-linear/screenshot.jpg` |
| 36 | A: Browser Use agent (bu-fast) | ab-ramp | ashby | yes | 7/7 | 0 | True | 34 | 207 s | $0.0191 | $0.0000 | ₹1.81 |  | `runs/20261105T190908-A-ab-ramp/screenshot.jpg` |
| 37 | A: Browser Use agent (bu-fast) | cu-workable-huggingface | custom | no | 5/8 | 0 | False | 41 | 260 s | $0.0293 | $0.0000 | ₹2.77 | agent could not attach the resume | `runs/20261105T191236-A-cu-workable-huggingface/screenshot.jpg` |
| 38 | A: Browser Use agent (bu-fast) | cu-recruitee-bunq | custom | yes | 6/6 | 0 | None | 17 | 125 s | $0.0114 | $0.0000 | ₹1.08 |  | `runs/20261105T191658-A-cu-recruitee-bunq/screenshot.jpg` |
| 39 | C: replay, no LLM | gh-gitlab | greenhouse | yes | 9/9 | 0 | True | 12 | 164 s | $0.0011 | $0.0000 | ₹0.11 |  | `runs/20261105T191945-C-gh-gitlab/screenshot.jpg` |
| 40 | C: replay, no LLM | gh-cloudflare | greenhouse | yes | 11/11 | 0 | True | 13 | 88 s | $0.0008 | $0.0000 | ₹0.07 |  | `runs/20261105T192231-C-gh-cloudflare/screenshot.jpg` |
| 41 | C: replay, no LLM | lv-spotify | lever | yes | 5/5 | 0 | True | 9 | 56 s | $0.0005 | $0.0000 | ₹0.04 |  | `runs/20261105T192359-C-lv-spotify/screenshot.jpg` |
| 42 | C: replay, no LLM | ab-linear | ashby | yes | 10/10 | 0 | True | 10 | 40 s | $0.0004 | $0.0000 | ₹0.03 |  | `runs/20261105T192457-C-ab-linear/screenshot.jpg` |
| 43 | C: replay, no LLM | ab-ramp | ashby | yes | 7/7 | 0 | True | 7 | 36 s | $0.0007 | $0.0000 | ₹0.07 |  | `runs/20261105T192538-C-ab-ramp/screenshot.jpg` |
| 44 | C: replay, no LLM | cu-recruitee-bunq | custom | no | 0/0 | 0 | None | - | 5 s | $0.0000 | $0.0000 | ₹0.00 | crashed: ConnectError: [WinError 10054] An existing connection was for | none |
| 45 | C: replay, no LLM | cu-recruitee-bunq | custom | no | 0/0 | 0 | None | 10 | 65 s | $0.0005 | $0.0000 | ₹0.05 | 10 action errors | `runs/20261105T192639-C-cu-recruitee-bunq/screenshot.jpg` |
