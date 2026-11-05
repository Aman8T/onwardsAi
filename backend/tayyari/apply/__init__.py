"""Auto-apply: match -> apply -> track -> learn, behind ``AUTO_APPLY_ENABLED`` (off by default).

The pieces, each behind a Protocol so every unit runs with fakes:

    intake       a pasted job link -> the ATS (Greenhouse, Lever, Ashby, Workday, unknown) and the job, through the
                 SSRF-guarded fetch; sites whose terms forbid automation are never opened (manual apply link instead)
    forms        the fields of an application form, read from its HTML (no browser needed, so tests use saved pages)
    playbook     a replayable per-ATS (or per-site) field map and step sequence; the runner follows it with no model call
    answers      the person's saved details and kept answers mapped onto fields; Haiku maps what rules cannot and drafts
                 screening answers that cite the person's facts (checked in code); anything uncertain goes to the person
    agent        the AI fallback: a `BrowserAgent` (Gemini Computer Use, Claude computer use) that fills the form when no
                 playbook can; every action is recorded and a successful run becomes a draft playbook
    engine       fill (stop before submit, screenshot and review) and submit (only with an approval of the exact digest)
    worker       the background loop that claims applications and runs the engine, outside the web request path
    stages       the stage state machine (engine events, manual changes, email classification)
    email        employers' emails -> stages (a Haiku classifier whose quote is checked to be in the email)
    metrics      playbook vs AI runs, success and failure reasons per ATS (the learning loop)
    service      what the routes call
"""
