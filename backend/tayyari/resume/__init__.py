"""/resume: one set of blocks, many resumes (R2, spec in docs/resume.md).

A person's blocks are the cited facts of their `match.models.FactBook`: a job, project or degree, or one bullet under
it. A resume is an arrangement of those blocks (which are in, in what order, in which words), stored as a
`contract.match.ResumeVariant`, so every line names the facts it rests on.

    arrange    `upload_variant` (the resume as the person wrote it) and `path_variant` (arranged for a next role)
    ops        `apply_ops`: move, leave out, put back, edit, reset; all of a request or none
    marks      how a draft differs from the uploaded resume; the blocks it leaves out
    finalise   the typed lines checked against the facts they cite; findings and their two answers
    service    `ResumeService`: the stored resumes, the default, the files of the finalised version
    errors     what the routes refuse

No routes, no tables. The package sits beside `match` and reuses its facts, selection, verifier, export and lint (no
fork); it sees storage only through `storage.ports` and never imports `api`. Leafout writes nothing here: every line is
a fact's own words, or the person's.
"""
