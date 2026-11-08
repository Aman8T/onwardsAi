"""Auto-apply: match -> apply -> track -> learn, behind ``AUTO_APPLY_ENABLED`` (off by default). Signed-in only.

The pieces, each behind a Protocol so every unit runs with fakes:

    intake       a pasted job link -> the ATS (Greenhouse, Lever, Ashby, Workday, unknown) and the job, through the
                 SSRF-guarded fetch; liveness through the ATS's public read endpoints; sites whose terms forbid automation
                 are never opened (manual apply link instead)
    checks       the start checks run before any row, hold, browser or model (signed in, live, fillable, not blocked,
                 not applied already, a credit free)
    netguard     the two-layer guard: the browser extension's rules and the route guard's decisions (every write and
                 every CAPTCHA provider blocked until the approved submit lifts it for the playbook's own hosts)
    driver       the `BrowserDriver` and `PageOpener` Protocols the runner drives; `browser_use` opens Browser Use
                 standalone browsers over CDP, `browser` is the Playwright page behind both layers
    scan, forms  the live form read from the page (frames, comboboxes, groups) and the saved-HTML reader
    playbook     the per-ATS field rules, open texts, submit and upload hosts and success signatures (no model)
    answers      the answer bank and details mapped onto fields, per-country work authorisation, self-ID defaults; Haiku
                 drafts custom answers that cite the person's facts (checked in code); the read-back digest
    fill_loop    the field-list loop (Haiku, then Sonnet) for what the rules could not place; code resolves every value
    runner       the preview fill (a review, nothing sent) and the approved submit (replay, marker, attach, one click)
    engine       one run of one application: stages, credit hold capture and release, spend, the screenshot, metrics
    worker       the background loop that sweeps stale rows, claims applications and runs the engine (in the app or as
                 its own process), concurrency 1 with a Browser Use back-off
    seams        what auto-apply is handed from outside: the spend reservation, the credit hold, the fact sink, the resume
    stages       the stage state machine (engine events, the person's moves, email classification)
    email        employers' emails -> stages (a Haiku classifier whose quote is checked to be in the email)
    metrics      playbook vs model runs, outcomes and failure reasons per ATS (the learning loop)
    service      what the routes call
"""
