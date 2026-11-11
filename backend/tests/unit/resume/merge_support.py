"""Second files for the merge tests: Meera's resume with a few lines changed, read the way a careful extractor would."""

from __future__ import annotations

from tayyari.contract.stored import StoredContext, StoredSource
from tayyari.match.facts import build_facts
from tayyari.match.models import FactBook
from tayyari.portfolio.facts import verify_facts
from tests.unit.match.support import Persona, meera, text
from tests.unit.portfolio._fixtures_a import scripted_facts

FILE_ID = "f" * 32
OTHER_ID = "e" * 32

LIFECYCLE = (
    "Led the lifecycle email and WhatsApp programme for 400,000 subscribers on CleverTap; "
    "repeat purchase rate rose from 22% to 31% in two quarters."
)
REWORDED = (
    "Ran the lifecycle email and WhatsApp programme for 400,000 subscribers using CleverTap, "
    "lifting repeat purchase from 22% to 31% within two quarters."
)
TESTS = "Ran 38 A/B tests on landing pages and checkout nudges; five winners shipped and added 6.4% to site conversion."
TESTS_40 = TESTS.replace("38", "40")
MENTORED = "Mentored 2 associates into channel owner roles within a year."
UNRELATED = "Designed a loyalty programme for the Mumbai pop-up stores."


def resume_text(changes: dict[str, str] | None = None) -> str:
    """Meera's resume with each key of ``changes`` replaced by its value (a key must be in the text)."""
    out = text("meera.resume.txt")
    for old, new in (changes or {}).items():
        assert old in out, old
        out = out.replace(old, new)
    return out


def book_of(content: str, file_id: str = FILE_ID, persona: Persona | None = None) -> FactBook:
    """The facts of a resume file, extracted the way the analysis does (scripted extraction, real checks)."""
    who = persona or meera()
    ctx = StoredContext(sources=[StoredSource(id=file_id, label="file", kind="resume", content=content)])
    facts = verify_facts(scripted_facts(ctx), ctx, profile=who.profile).facts
    return build_facts(who.profile, ctx, facts)


def existing_book() -> FactBook:
    return meera().book()
