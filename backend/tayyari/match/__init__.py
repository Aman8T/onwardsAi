"""Posting match: pure services that turn a person's evidence and a pasted job posting into an evidence-cited match.

    models      the wire contract (re-exported from `contract.match`) and the internal `FactBook`
    text        folding and comparison helpers (quotes, terms, hashes)
    aliases     the two small alias tables (skill spellings, domain words)
    schemas     what the models are asked to return, and the JSON Schemas built from it
    calls       how a model call is made (purpose, failure policy)
    facts       the fact book: portfolio facts regrouped into bullets and skills, plus user facts
    posting     read a pasted posting on its own into requirements and gates (no person in the prompt)
    gate        answer the gate questions and decide, in code, whether the match is blocked
    coverage    check requirements against facts; downgrades, counts, band and confidence computed by code
    radar       the role-versus-you radar from requirement weights and the graph's domains
    questions   at most three questions for the gaps and unknowns
    verify      the sentence verifier (anchors, numbers, scope verbs, posting terms)
    bullets     settle one resume sentence (placeholders, stretch, term questions); apply the person's answers
    variant     compose the resume for one posting from cited facts: relevance, selection, skills, phrasing
    cover       compose the cover note from (requirement, fact) pairs
    export      DOCX and PDF of the resume and the cover note
    lint        read the exported files back and check them
    service     the pipeline (`MatchService`)
    ports       the Protocols the persistence wave implements

No routes, no tables. It sees storage only through `ports`, and the portfolio only for its fact extractor and its
sentence anchors.
"""
