"""The evidence check: every quote that backs a strong skill is checked against its source, unsupported ones go.

After `to_profile`, each evidence item of every skill scoring at least `EVIDENCE_MIN_SCORE` becomes an
`EvidenceClaim` (with that source's raw text, the cleaned reader output and the deterministic facts) and is
sent to `DecisionService.check_evidence`. Items the service rules out (``keep=False``) are removed from the
profile; everything else stays. The whole check has `EVIDENCE_TIMEOUT_S`: on a timeout, an error or an answer
that does not line up with the claims, nothing is dropped and nothing is shown. Gap skills (below the score) state
an observed absence and are not checked.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from tayyari.contract import Profile, ev_log
from tayyari.core.logging import get_logger
from tayyari.core.text import plural
from tayyari.decisions.service import EvidenceClaim, NullDecisionService

from .normalise import ReadResult
from .runctx import RunContext

log = get_logger(__name__)

EVIDENCE_MIN_SCORE = 45
EVIDENCE_TIMEOUT_S = 4.5
EVIDENCE_WHO = "Jev"


@dataclass(frozen=True, slots=True)
class EvidenceOutcome:
    """What one evidence check did: how many quotes were checked and how many were removed."""

    checked: int
    dropped: int

    @property
    def line(self) -> str:
        """The log line for the analysis screen."""
        return f"Checked {plural(self.checked, 'evidence quote')}, dropped {self.dropped}"


def build_claims(
    profile: Profile, results: Sequence[ReadResult]
) -> tuple[list[EvidenceClaim], list[tuple[int, int]]]:
    """The claims to check, and where each one lives as ``(skill index, evidence index)`` in ``profile``."""
    by_source = {r.planned.id: r for r in results}
    claims: list[EvidenceClaim] = []
    where: list[tuple[int, int]] = []
    for si, skill in enumerate(profile.skills):
        if skill.score < EVIDENCE_MIN_SCORE:
            continue
        for ei, ev in enumerate(skill.evidence):
            result = by_source.get(ev.sourceId)
            raw = result.raw if result else None
            claims.append(
                EvidenceClaim(
                    skill_id=skill.id,
                    source_id=ev.sourceId,
                    text=ev.text,
                    source_text=(raw.text if raw else "") or "",
                    reader_output=result.reader if result else None,
                    facts=raw.meta if raw else None,
                )
            )
            where.append((si, ei))
    return claims, where


async def check_evidence(
    run: RunContext, profile: Profile, results: Sequence[ReadResult]
) -> EvidenceOutcome | None:
    """Check and prune ``profile``'s evidence in place, send the summary line; None when no check completed.

    With decisions switched off (`NullDecisionService`: Jev disabled or no key) nothing is checked and nothing is shown.
    """
    if isinstance(run.decisions, NullDecisionService):
        return None
    claims, where = build_claims(profile, results)
    if not claims:
        return None
    try:
        async with asyncio.timeout(EVIDENCE_TIMEOUT_S):
            verdicts = await run.decisions.check_evidence(claims, run.decision_ctx)
    except TimeoutError:
        log.info("evidence_check_timed_out", claims=len(claims))
        return None
    except Exception as exc:
        log.warning("evidence_check_failed", claims=len(claims), error_type=type(exc).__name__)
        return None
    if len(verdicts) != len(claims):
        log.warning("evidence_check_mismatch", claims=len(claims), verdicts=len(verdicts))
        return None

    drop: dict[int, set[int]] = {}
    for (si, ei), verdict in zip(where, verdicts, strict=True):
        if not verdict.keep:
            drop.setdefault(si, set()).add(ei)
    for si, indexes in drop.items():
        skill = profile.skills[si]
        skill.evidence = [e for i, e in enumerate(skill.evidence) if i not in indexes]
        if not skill.evidence:
            log.info("skill_lost_evidence", skill_id=skill.id)

    outcome = EvidenceOutcome(checked=len(claims), dropped=sum(len(v) for v in drop.values()))
    run.send(ev_log(EVIDENCE_WHO, outcome.line))
    return outcome


__all__ = [
    "EVIDENCE_MIN_SCORE",
    "EVIDENCE_TIMEOUT_S",
    "EVIDENCE_WHO",
    "EvidenceOutcome",
    "build_claims",
    "check_evidence",
]
