# Adopting a new Jev version

Jev answers most of the small judgement calls in Tayyari: the first read on the analysis screen, source triage, the
evidence check and the chat web-search gate, plus the shadow numbers logged next to Sonnet's. A new Jev version can
change every one of those answers and every confidence value that the thresholds in
`tayyari/decisions/defaults.toml` were tuned on. This folder is how a team tries a new version on **real past
traffic** before it takes any work, and decides which thresholds it can be trusted at.

The loop is: **export** what the live app asked and got, **replay** the same questions against the new model, **report**
how the two differ, **promote** the thresholds the evidence supports, change the pin, deploy.

```
 Neon decision log --export--> corpus/<date>.jsonl --replay--> reports/replay-<model>-<ts>.jsonl
                                                                        |
                                    labels/*.jsonl (optional) ----------+--eval report--> <replay>.report.json
                                                                        +--eval promote--> defaults.toml patch
```

All commands run from `backend/`:

```
uv run python -m tayyari.cli export  --help
uv run python -m tayyari.cli replay  --help
uv run python -m tayyari.cli eval report  --help
uv run python -m tayyari.cli eval promote --help
```

On Windows set `PYTHONUTF8=1` as in `DEPLOY.md`. PowerShell and cmd do not expand `*.jsonl`; the commands expand globs
themselves, so quote or not as you like.

## What lives here

| Folder | Holds | In git |
| --- | --- | --- |
| `corpus/` | exports of the decision log (`export` writes here) | never: contains resume and page text |
| `labels/` | hand labels you write | yes: decision ids and answers only, no text |
| `reports/` | replays, reports and proposals | no (ignored) |

`corpus/*.jsonl` files carry the **state that was sent to Jev**: resume text, notes and page text. Contact data
(emails, phone numbers, address lines) is masked in them unless `TAYYARI_JEV__SEND_PII` was on when the decision was
logged. Treat the files as personal data: never commit them (`.gitignore` should list `backend/evals/corpus/*.jsonl`
next to `reports/`), keep them off shared drives and out of chat tools, and delete them when the adoption is done.
`evals/` is excluded from the FastAPI Cloud upload (`.fastapicloudignore`), so none of it is deployed.

## Before you start

- **Retention.** Jev payloads and states are purged `retention.payload_days` (30 days) after they were written
  (`storage/maintenance.py`); older decision rows keep their metadata but cannot be replayed. Export while the window
  you care about is still inside it. The permanent sample corpus (the bundled persona, CLI and eval runs) is exempt
  from the purge; `--include-sample` brings it in.
- **Read-only access.** Make a database role that can only read the three decision tables, and use its connection
  string. With Neon, in the SQL editor of the production branch:

  ```sql
  CREATE ROLE evals_reader WITH LOGIN PASSWORD '<a long random password>';
  GRANT USAGE ON SCHEMA public TO evals_reader;
  GRANT SELECT ON decision_log, jev_calls, decision_states TO evals_reader;
  ```

  Copy the connection string of that role from the Neon console (the pooled `-pooler` host works). `export` never
  migrates, writes or deletes, but a role that cannot write makes that a fact of the database, not of the code.
  Drop the role when you are done: `DROP OWNED BY evals_reader; DROP ROLE evals_reader;`.
- **A Jev key.** `replay` uses the real key from `JEV_API_KEY` (in `backend/.env` or the environment). `export` and
  `eval` need none. The replay ignores the `TAYYARI_JEV__ENABLED` kill switch: the switch guards the live app, a replay
  never touches it.

## 1. Export

```
uv run python -m tayyari.cli export --since 14d
```

Put the connection string in the **environment**, not on the command line (the command line lands in your shell
history; `--database-url` exists for scripts):

```powershell
$env:DATABASE_URL = "postgresql://evals_reader:...@ep-xxx-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require"
uv run python -m tayyari.cli export --since 14d
Remove-Item Env:DATABASE_URL
```

```bash
read -rs DATABASE_URL && export DATABASE_URL     # paste it, nothing is echoed
uv run python -m tayyari.cli export --since 14d
unset DATABASE_URL
```

Without `DATABASE_URL` the command reads your local SQLite database, which is handy for trying the tools out.

| Option | Meaning |
| --- | --- |
| `--since WHEN` | start of the window (required): `7d`, `12h`, `90m`, `2w`, `now` or an ISO date such as `2026-10-01` |
| `--until WHEN` | end of the window, exclusive (default: now) |
| `--spec PATTERN` | only these spec ids; a glob such as `first_read.*`; repeat it or separate with commas |
| `--out FILE` | default `evals/corpus/<today>.jsonl`; an existing file is kept unless you pass `--force` |
| `--include-sample` | also export the bundled sample persona's rows |
| `--limit N` | stop after N lines |

One JSONL line per decision that can be asked again:

```
id, created_at, spec_id, spec_version, pack, subject_ref, mode, jev_model, state_hash, state,
question, jev_answer, jev_confidence, claude_answer, incumbent, used, source_used
```

`state` is what was sent to Jev, `question` the wire question of this decision (`subject_ref` is the question key,
for example `skill.level#python`), `jev_answer` the normalised answer of the model named in `jev_model`,
`claude_answer` the Claude fallback or audit answer when there was one, and `incumbent` the value the decision was
compared with (Sonnet's numbers, for the shadow specs). The summary names every row that was left out and why:
no Jev call was made, the payload was purged, or it belongs to the sample persona.

**How much?** `eval promote` wants at least 30 scored decisions per primary spec above a threshold, so aim for a few
hundred rows per spec. `skill.level`, `requirement.have`, `role.closest` and `role.fit` produce many rows per analysis;
`first_read.*` and `triage.*` one to a few per analysis. Two weeks of traffic is usually enough; for a rare spec widen
`--since` or use `--spec` to export only the specs you are short of.

## 2. Replay

Always look at the plan first. It sends nothing and needs no key:

```
uv run python -m tayyari.cli replay --corpus evals/corpus/2026-10-08.jsonl --model jev-1.14.0 --dry-run --shuffle 2
```

```
Replay plan (dry run: nothing is sent)
  corpus:     evals/corpus/2026-10-08.jsonl (3,210 rows, 3,198 can be asked)
  questions:  3,102 distinct, over 541 states
  Jev calls:  612 + 1,102 shuffled (2 per call with choices)
  input:      about 9,400,000 tokens (request bytes / 4)
  model:      jev-1.14.0 at $0.042 per million input tokens: about $0.39
```

(The numbers are an example.) The cost is an estimate from the request size; Jev bills input tokens only. A model id
the price table does not know is priced at the highest known Jev rate (add the new id to `JEV_PRICES` in
`tayyari/llm/models.py` when you adopt it). Then a small real run, and the full one:

```
uv run python -m tayyari.cli replay --corpus evals/corpus/2026-10-08.jsonl --model jev-1.14.0 --limit 50
uv run python -m tayyari.cli replay --corpus evals/corpus/2026-10-08.jsonl --model jev-1.14.0 --shuffle 2
```

- **What is sent.** Decisions are grouped by `(state hash, pack)`, so each original Jev call is sent once with its
  questions. Identical questions of one state are asked once and shared; more than 30 questions over one state are
  split into several calls, as the live runner does. `--concurrency 8` requests run at a time; a 429 waits for
  `Retry-After` and tries again; a 401 stops the run; ten failed calls before the first success also stop it (a
  misspelled model id). `--timeout 30` is per attempt, far above the live budgets (2.5 to 8 s), because nobody is waiting.
- **`--shuffle N`** sends the choice questions N more times with their options in a seeded random order (`--seed`,
  default 0), with the wording and option descriptions unchanged. A model that reads the content answers the same
  whatever the order; one that follows the order flips. The report counts the flips.
- **Ctrl-C** keeps what was answered so far.
- **Output**: `evals/reports/replay-<model>-<timestamp>.jsonl` (change it with `--out`), one line per decision in corpus
  order, without the state:

  ```
  id, spec_id, spec_version, pack, subject_ref, key, state_hash, kind, mode, used, source_used, replay_model,
  old {model, answer, confidence}, new {model, answer, confidence} | null,
  claude_answer, incumbent, shuffle {runs, answers, flips} | null, cost_micro_usd, latency_ms, error
  ```

  `new.answer` is normalised exactly as the live app normalises an answer (`JevAnswer`): the winning label of a choice,
  the probability of yes for a yes/no, the level of a score, and one `confidence` on the same scale the thresholds use.
  Rows that could not be answered carry `new: null` and the reason in `error`.

## 3. Labels (optional, and worth the hour)

Agreement is only as good as its reference. Per decision the report uses the first of:

1. a **hand label** from `evals/labels/*.jsonl`,
2. the **Claude** answer stored with the decision (the fallback or audit answer),
3. the **incumbent** value (Sonnet's number for a shadow spec), put in the spec's terms: a skill or fit score is compared
   with the band (under 40, 40 to 59, 60 to 74, 75 to 89, 90 and up) of the level the new model gave, a requirement with
   its `have` flag, the closest role with its `role_id`,
4. the **old Jev** answer.

Agreement with the old Jev is consistency, not accuracy, and agreement with Sonnet's numbers measures how like Sonnet
the model is, not how right. Hand labels are what makes the thresholds trustworthy. A label file is JSONL:

```json
{"id": "019ba2c4-...", "label": "design_ux"}
{"id": "019ba2c5-...", "label": false}
{"id": "019ba2c6-...", "label": 3}
{"state_hash": "9f2c...", "key": "first_read.depth", "label": 2}
```

The id is the decision id from the corpus and the replay. The label is in the terms of the question: the option label
for a choice, `true` or `false` for a yes/no, the level number for a score. A label tied to `state_hash` and `key`
applies to every decision that asked that question over that state. A good source of lines to label is the rows where
the new model and Claude disagree. For choice questions, for example:
`jq -c 'select(.kind == "choice" and .claude_answer.choice != null and .new.answer.choice != .claude_answer.choice) | {id, key, new: .new.answer.choice, claude: .claude_answer.choice}'`
on the replay file.

## 4. Read the report

```
uv run python -m tayyari.cli eval report --replay evals/reports/replay-jev-1.14.0-20261008T093015Z.jsonl --labels evals/labels/*.jsonl
```

It prints three tables and writes the same numbers as JSON next to the replay (`<replay>.report.json`).

- **Per spec**: `rows`, `fail` (calls that errored), `agree` (the new answer equals the reference), `old` (the old Jev
  against the same reference, left out where the reference is the old Jev itself), `thr` (the threshold in
  `defaults.toml`), `esc` (the escalation rate: the share of answers below `thr`, which the runner sends to the
  fallback or leaves neutral), `used` (agreement of the answers at or above `thr`, the ones the app would act on),
  `order` (the share of shuffled choice questions whose answer changed with the option order) and `cost`.
- **Accuracy by confidence** of the new answer in the bins 0.0-0.5, 0.5-0.7, 0.7-0.9 and 0.9-1.0, with the number of
  scored rows. Confidence is only useful if accuracy rises with it.
- **What the agreement was measured against**: how many rows used a label, Claude, the incumbent or the old Jev.

What to look for: `agree` at least as high as `old`; `used` clearly above `agree`; accuracy that climbs from bin to
bin; `order` close to 0; few `fail`; `esc` low enough that the fallback bill does not grow. A shadow spec
(threshold 1.0) shows `esc` near 100 percent, which is expected: nothing from it is used yet.

## 5. Promote thresholds

```
uv run python -m tayyari.cli eval promote --replay evals/reports/replay-jev-1.14.0-20261008T093015Z.jsonl --target 0.9 --dry-run
```

For each **primary** spec in `defaults.toml`, it finds the lowest threshold (in steps of 0.01) such that the answers
at or above it agree with the reference at `--target` (default 0.9) or better, over at least `--min-rows` (default 30)
answers, and prints a table and a unified diff of `defaults.toml`. Agreement is not monotonic in the threshold, so every
step is tried. A spec that cannot reach the target, has too few rows or is not in the replay keeps its threshold, and
the note says why. Shadow specs are never touched: promoting one means editing its `mode` and `threshold` in
`defaults.toml` by hand once the report convinces you.

It **never edits** `defaults.toml`. Without `--dry-run` it writes the proposal as JSON (`<replay>.promote.json`) and the
diff alone (`<replay>.promote.patch`) next to the replay; apply the patch yourself, from the repository root:

```
git apply --ignore-whitespace backend/evals/reports/replay-jev-1.14.0-20261008T093015Z.promote.patch
```

(or edit the `threshold = ...` lines by hand). When most of the scored rows were judged against the old Jev or
Sonnet's numbers the command warns you: add labels first.

## 6. Change the pin and deploy

1. Apply the threshold patch and review it like any code change.
2. Add the new model id and its input price to `JEV_PRICES` in `tayyari/llm/models.py`, so the spend ledger is exact.
3. Change the pin: the default of `JevSettings.model` in `tayyari/core/settings.py` (every decision row records the
   model that answered, so old and new rows never mix), or just set `TAYYARI_JEV__MODEL=jev-1.14.0` in the
   environment of the deployment to try it first.
4. Run the tests and the smoke check: `uv run pytest -q`, then `uv run python -m tayyari.cli smoke` (one real Jev call).
5. Deploy (`uv run python -m tayyari.cli deploy`). Watch `decision_log` for the new `jev_model`: error rate, `esc`
   rate and the Claude audit agreement (`extra.audit_agrees`, 5 percent of used `evidence.support` and `chat.web_gate`
   answers).
6. Rolling back needs no deploy: set `TAYYARI_JEV__MODEL` back, or `TAYYARI_JEV__ENABLED=false` to send every primary
   decision to its Claude fallback, or `TAYYARI_DECISION_OVERRIDES='{"first_read.*": {"threshold": 0.9}}'` to raise
   a family of thresholds.

Re-run the export-replay-report loop a few weeks after the switch, on rows answered by the new model, to confirm the
thresholds still hold.
