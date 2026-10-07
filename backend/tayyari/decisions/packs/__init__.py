"""The concrete decision packs (first read, triage, evidence, chat gate, shadow) arrive in Wave 2.

Each pack module defines its `DecisionSpec`s and `DecisionPack` on top of `tayyari.decisions.spec`, and the
`DecisionService` implementation (`tayyari.decisions.service`) registers them in a `DecisionRegistry`.
"""
