"""Spec 1 rule 3 and section 7's guard test: a full preview of a Greenhouse, a Lever and an Ashby form (and a board with
comboboxes, and a form the field-list loop finishes) sends nothing to the employer. No write gets past the guard, even
when the page's own scripts try one; the resume input stays empty; ``allow_submit_hosts`` is never called."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from tayyari.apply.answers import BankEntry, FillContext, Resume
from tayyari.apply.fill_loop import FieldListLoop
from tayyari.apply.netguard import READ_METHODS
from tayyari.apply.playbook import ASHBY, GREENHOUSE, LEVER, Playbook
from tayyari.apply.runner import preview
from tayyari.core.settings import ApplySettings
from tests.fakes.apply_browser import FakeDriver
from tests.fakes.claude import FakeClaudeClient

DATA = Path(__file__).resolve().parents[2] / "data" / "apply"


def _html(name: str) -> str:
    return (DATA / name).read_text(encoding="utf-8")


def _scan(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((DATA / "forms" / name).read_text(encoding="utf-8"))
    return data


CASES = [
    ("greenhouse", _html("greenhouse_job.html"), GREENHOUSE, "https://job-boards.greenhouse.io/acme/jobs/4012345",
     '[id="resume"]'),
    ("lever", _html("lever_apply.html"), LEVER, "https://jobs.lever.co/acme/0f8f8b3c/apply",
     '[id="resume-upload-input"]'),
    ("ashby", _html("ashby_application.html"), ASHBY, "https://jobs.ashbyhq.com/acme/0a1b2c3d/application",
     '[id="_systemfield_resume"]'),
    ("greenhouse-board", _scan("greenhouse-react.json"), GREENHOUSE, "https://job-boards.greenhouse.io/acme/jobs/7000001",
     '[id="resume"]'),
]  # fmt: skip


def ctx() -> FillContext:
    details = {"firstName": "Riya", "lastName": "Kapoor", "email": "riya@example.com", "phone": "+91 98765 43210",
               "linkedin": "https://linkedin.com/in/riya", "location": "Pune, India", "noticePeriod": "30 days",
               "currentCompany": "Fintech Co"}  # fmt: skip
    return FillContext(
        resume=Resume("Riya-Kapoor.pdf", b"%PDF-1.7 fake"),
        typed={"question_102": "Because of the platform."},
        bank={
            **{k: BankEntry(v) for k, v in details.items()},
            "needsSponsorship:IN": BankEntry("No", "first_check"),
        },
    )


@pytest.mark.parametrize(("name", "page", "playbook", "url", "resume"), CASES, ids=[c[0] for c in CASES])
async def test_a_preview_sends_nothing_and_leaves_the_resume_empty(
    name: str, page: Any, playbook: Playbook, url: str, resume: str
) -> None:
    quiet = FakeDriver(page, url=url, upload_url=f"https://{url.split('/')[2]}/uploads")
    result = await preview(quiet, playbook, url, ctx(), None)
    assert result.status in ("ready", "questions") and result.fields
    assert all(method in READ_METHODS for method, _ in quiet.requests)  # no non-GET was even tried
    assert quiet.allow_calls == [] and not quiet.write_hosts and not quiet.submitted
    assert "upload" not in [op for op, _, _ in quiet.actions]
    assert quiet.values.get(resume) is None
    scan = await quiet.scan()
    assert all(not f.value for f in scan.fields if f.kind == "file")
    assert result.resume is not None and result.resume.selector == resume

    beacon = f"https://{url.split('/')[2]}/autosave"
    noisy = FakeDriver(page, url=url, page_posts=[beacon])
    await preview(noisy, playbook, url, ctx(), None)
    tried = [(m, u) for m, u in noisy.requests if m not in READ_METHODS]
    assert tried and all(r in noisy.blocked for r in tried)  # the page's own writes were all stopped
    assert noisy.sent_writes() == [] and noisy.allow_calls == []


async def test_a_loop_preview_sends_nothing_either() -> None:
    custom = _scan("careers-custom.json")

    def reply(call: Any) -> dict[str, Any]:
        return {"actions": [{"field": 0, "valueKey": "location", "op": "fill", "choice": "", "confidence": "high"}],
                "clicks": ["Send application"], "anomalies": [], "blocked": ""}  # fmt: skip

    claude = FakeClaudeClient()
    claude.script_structured("apply.map", reply, repeat=True)

    @asynccontextmanager
    async def reserve(usd: float) -> AsyncIterator[None]:
        yield

    loop = FieldListLoop(claude, None, reserve, ApplySettings())
    driver = FakeDriver(custom, url=custom["url"], page_posts=["https://careers.example.com/track"],
                        buttons=["Send application"])  # fmt: skip
    result = await preview(driver, Playbook(ats="generic"), custom["url"], ctx(), None, loop)
    assert claude.calls_for("apply.map") and "Send application" in result.refused
    assert driver.sent_writes() == [] and driver.allow_calls == [] and not driver.submitted
