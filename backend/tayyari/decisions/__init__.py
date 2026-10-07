"""Jev decisions: the framework that lets a better Jev take on more work without code changes.

    spec       DecisionSpec, DecisionPack, DecisionConfig, DecisionResult, PackResult
    config     defaults.toml + environment overrides + the kill switch, as a DecisionConfigStore
    registry   DecisionRegistry: the packs and specs of the build
    runner     DecisionRunner: one Jev call per pack and state, modes off / shadow / primary, Claude fallback, audit
    fallback   the Haiku rubric derived from a question (schema, prompt, answer mapping)
    recorder   DecisionRecorder: buffered, deduplicated, compressed logging of states, Jev calls and decisions
    service    the facade analysis and chat call (DecisionService Protocol, its inputs and results, a null service)

`decisions` sits above `llm` and below `analysis` / `chat` in the layering rule. Nothing is imported eagerly:
import the submodule you need.
"""
