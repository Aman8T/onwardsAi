"""Resume → Portfolio: turns a profile (and its stored source text) into a portfolio from a closed kit.

    kit        the catalog: compositions, slots per block, minimum content per block, style options
    features   graph-shape metrics and content counts (plus Jev's first-read answers) the composer reads
    facts      Haiku extracts structured resume facts, each kept only when its quote is found in the source
    compose    the deterministic designer: features + seed -> up to three valid `PortfolioSpec`s, with reasons
    snapshot   the frozen graph, proof stats, person and open-to lines built from the profile (no model)
    copy       Sonnet writes the copy slots from the facts only
    ground     validates every slot sentence against the quotes of the facts it cites (code, then Jev)
    fallback   deterministic slot wording from the facts themselves (always grounded)
    public     the viewer's `PublicPortfolio` (visible + public blocks, opted-in details, skills >= 45)
    edit       applies a `PortfolioPatch` with the kit's rules
    service    the NDJSON generation stream, section rewrite, and the reads/edits the routes call

Nothing here imports analysis or chat; it sees storage through `storage.ports` only.
"""
