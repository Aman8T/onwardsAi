"""The concrete decision packs of this build, one module each, and the `DecisionService` that runs them.

    first_read   the instant read of the resume for the analysis screen (primary, display only)
    triage       skip a login wall or an empty page, label the rest (primary, label or skip with code agreement)
    evidence     keep or drop an evidence claim: code checks first, Jev for the claims whose anchors are missing
    chat_gate    withhold web tools only for a message Jev is sure the profile can answer
    shadow       Jev's own skill, requirement and role numbers next to Sonnet's (logged, never used)
    common       helpers they share
    service_impl JevDecisionService (the DecisionService the analysis and chat services call) and build_registry()

Every spec id here has a table in ``decisions/defaults.toml``; `service_impl.build_registry` lists them all so the
container can log config ids that match no spec. Import the submodule you need: nothing is imported eagerly.
"""
