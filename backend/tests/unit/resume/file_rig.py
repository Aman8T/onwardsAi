"""A `ResumeService` with files, over the fakes, and a model that reads the resume it is shown.

The scripted extraction answers for whatever resume text the prompt holds (the numbered lines of its `<source>` block),
the way a careful model would, so a second file uploaded as bytes goes through the real reader, the real prompt and the
real checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from tayyari.contract.resume import ResumeDetail, ResumeList
from tayyari.contract.stored import StoredContext, StoredSource
from tayyari.llm.types import CallContext
from tayyari.match.service import MatchService
from tayyari.resume.service import Holder, ResumeService
from tayyari.storage.ports import StoredResumeFile
from tests.fakes.claude import FakeClaudeClient, StructuredCall
from tests.fakes.repos import FakeRepositories
from tests.unit.match.support import CTX, HAIKU, Persona, meera
from tests.unit.portfolio._fixtures_a import scripted_facts
from tests.unit.resume.support import PID, T0, TickingClock

_ROW = re.compile(r"^\[(\d+)\] (.*)$", re.MULTILINE)
_SOURCE = re.compile(r'<source id="([^"]+)" kind="([^"]+)"[^>]*>\n(.*?)\n</source>', re.DOTALL)


def read_back(call: StructuredCall) -> StoredContext:
    """The sources of an extraction prompt, rebuilt from their numbered lines."""
    sources: list[StoredSource] = []
    for sid, kind, body in _SOURCE.findall(call.user_text):
        rows = {int(n): line for n, line in _ROW.findall(body)}
        content = "\n".join(rows.get(i, "") for i in range(1, max(rows, default=0) + 1))
        sources.append(StoredSource(id=sid, label=sid, kind=kind, content=content))
    return StoredContext(sources=sources)


def scripted(call: StructuredCall) -> dict[str, object]:
    return scripted_facts(read_back(call))


@dataclass
class FileRig:
    persona: Persona
    service: ResumeService
    repos: FakeRepositories
    claude: FakeClaudeClient
    holder: Holder

    async def upload(self, name: str, data: bytes, content_type: str | None = None):  # type: ignore[no-untyped-def]
        return await self.service.upload_file(
            self.holder,
            self.persona.profile,
            self.persona.context,
            filename=name,
            content_type=content_type,
            data=data,
            call_ctx=CTX,
        )

    def ctx(self) -> CallContext:
        return CTX

    async def listing_first(self) -> ResumeList:
        return await self.service.listing(self.holder, self.persona.profile, self.persona.context, CTX)

    async def path(self) -> ResumeDetail:
        return await self.service.path(
            self.holder, self.persona.profile, self.persona.context, "senior-growth", CTX
        )

    def analysis_file(self) -> StoredResumeFile:
        """The file the profile was analysed from, as the analysis keeps it (the oldest file of the profile)."""
        return StoredResumeFile(
            id="c" * 32,
            user_id=None,
            profile_id=PID,
            origin="analysis",
            filename="analysis.pdf",
            mime="application/pdf",
            size=13,
            sha256="0" * 64,
            text="the analysed text",
            facts={},
            merge=None,
            merged_book=None,
            created_at=T0 - timedelta(days=1),
        )

    def extractions(self) -> int:
        return len(self.claude.calls_for("match.facts"))  # type: ignore[arg-type]


def file_rig(*, user_id: str | None = "u1") -> FileRig:
    who = meera()
    claude = FakeClaudeClient()
    claude.script_structured("match.facts", scripted, repeat=True)  # type: ignore[arg-type]
    claude.script_structured(
        "match.relevance",
        {"scores": [{"factId": "exp-1.3", "score": 96, "requirementIds": ["r1"]}]},
        repeat=True,
    )
    repos = FakeRepositories()
    match = MatchService(claude=claude, model=HAIKU, facts_cache=repos.profile_facts)
    service = ResumeService(
        claude=claude,
        model=HAIKU,
        match=match,
        resumes=repos.resumes,
        user_facts=repos.user_facts,
        clock=TickingClock(),
        files=repos.resume_files,
    )
    return FileRig(who, service, repos, claude, Holder(PID, user_id, True))
