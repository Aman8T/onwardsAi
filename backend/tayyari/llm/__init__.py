"""LLM interfaces: the types, price tables, typed errors and Protocols every service depends on.

`llm` sits above `core` and `contract` in the layering rule (core < contract < llm < decisions < analysis/chat
< api). Nothing here does I/O; the implementations come in later modules.

    types      CallContext, TextBlock, Usage, StructuredResult, chat stream events, Purpose, Effort
    models     ModelSpec and MODEL_SPECS (prices, capabilities), spec_for, claude_cost_micro, jev_cost_micro
    errors     ClaudeError family, from_anthropic, SpendCeilingReached, JevError
    usage      UsageSink (Null and Memory sinks), usage_from_message
    claude     the ClaudeClient Protocol (structured, stream_chat)
    jev        JevQuestion, JevAnswer, normalise_answer, JevResponse, the JevClient Protocol
    breaker    CircuitBreaker for the Jev API

Import the submodule you need; this package imports nothing eagerly. Fakes for every Protocol are in
tests/fakes/{claude,jev,clock}.py.
"""
