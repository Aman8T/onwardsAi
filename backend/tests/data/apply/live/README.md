Answers of the ATS public read endpoints that `apply.intake.check_live` uses, hand-made in the documented shapes. They
are not saved live responses: confirm each against the real endpoint in A1.6 and replace it with the saved answer.

| File | Endpoint | Status |
|---|---|---|
| `greenhouse_job_open.json` | `GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}` | 200 |
| `greenhouse_job_closed.json` | the same, for a removed job | 404 |
| `lever_posting_open.json` | `GET https://api.lever.co/v0/postings/{site}/{id}` | 200 |
| `lever_posting_closed.json` | the same, for a removed posting | 404 |
| `ashby_board.json` | `GET https://api.ashbyhq.com/posting-api/job-board/{board}` (open jobs only) | 200 |

Open questions for A1.6: whether Greenhouse and Ashby leave unlisted (link-only) jobs out of these answers, which
would make an open unlisted job look closed; and the EU hosts (`boards-api.eu.greenhouse.io`, `api.eu.lever.co`).
