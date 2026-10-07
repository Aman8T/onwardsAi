"""End to end proof that a scanned resume now takes the "attach the PDF" path of the live (legacy) pipeline.

`legacy.pipeline.read_one` attaches `SourceDoc.pdf_bytes` to the reader call only when the source's text is
empty, and treats a source as readable only when `ok` is True. Before the ingest fix the resume ingester put
placeholder text into scanned PDFs, so that branch could never run. This test drives the real pipeline with a
fake model client and watches what the reader call receives.

The legacy package is deleted in the last rebuild wave; this test goes with it (it skips when it is missing).
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from tests.unit.ingest.helpers import make_pdf

pipeline = pytest.importorskip("tayyari.legacy.pipeline")
legacy_schemas = pytest.importorskip("tayyari.legacy.schemas")
legacy_types = pytest.importorskip("tayyari.legacy.types")

RESUME_LINES = [
    "Riya Kapoor, Frontend Engineer",
    "Experience",
    "Kitebird - built the checkout in React and TypeScript, mentored two interns, shipped weekly.",
    "Skills",
    "React, TypeScript, Node.js, GraphQL, Figma, Accessibility, Storybook and design systems.",
]

READER_OUTPUT: dict[str, Any] = {
    "sub": "1 page",
    "summary": "A frontend engineer's resume.",
    "person": {"name": "Riya Kapoor", "currentRole": "Frontend Engineer"},
    "signals": [],
    "skills": [{"name": "React", "evidence": "Built the checkout in React", "strength": 4}],
    "notes": ["Add measurable outcomes."],
}


def synth_output() -> dict[str, Any]:
    def skill(name: str, domain: str) -> dict[str, Any]:
        return {
            "id": name.lower(),
            "name": name,
            "domainId": domain,
            "score": 70,
            "evidence": [{"sourceId": "resume", "text": f"Used {name}"}],
            "unlocks": [],
        }

    return {
        "person": {
            "name": "Riya Kapoor",
            "currentRole": "Frontend Engineer",
            "headline": "Frontend Engineer",
            "summary": "s",
        },
        "mastery": {"score": 70, "insight": "Solid frontend depth."},
        "domains": [
            {"id": "frontend", "name": "Frontend", "short": "Frontend", "score": 72, "summary": "x"},
            {"id": "practice", "name": "Practice", "short": "Practice", "score": 60, "summary": "y"},
        ],
        "skills": [
            skill("React", "frontend"),
            skill("TypeScript", "frontend"),
            skill("Figma", "practice"),
            skill("Testing", "practice"),
        ],
        "roles": [
            {
                "id": "senior-frontend",
                "title": "Senior Frontend Engineer",
                "alignment": 80,
                "eta": "~6 months",
                "why": "Close already.",
                "requirements": [
                    {"name": "React", "skillId": "react", "have": True, "current": 70, "target": 80}
                ],
            }
        ],
        "sources": [{"id": "resume", "sub": "1 page", "notes": ["n"]}],
    }


class FakeModel:
    """Stands in for `run_structured`: records every call and answers readers and the synthesizer."""

    def __init__(self) -> None:
        self.reader_calls: list[dict[str, Any]] = []
        self.synth_calls: list[dict[str, Any]] = []

    async def __call__(
        self, model: str, system: str, prompt: str, schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        call = {"model": model, "prompt": prompt, "pdf": kwargs.get("pdf")}
        if schema is legacy_schemas.READER_SCHEMA:
            self.reader_calls.append(call)
            return dict(READER_OUTPUT)
        self.synth_calls.append(call)
        return synth_output()


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    fake = FakeModel()
    monkeypatch.setattr(pipeline, "run_structured", fake)

    async def no_save(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(pipeline.store, "save_profile", no_save)
    monkeypatch.setattr(pipeline.store, "save_context", no_save)
    return fake


async def analyse(resume: bytes, name: str) -> list[dict[str, Any]]:
    inp = legacy_types.AnalyzeInput(resume_bytes=resume, resume_name=name)
    return [event async for event in pipeline.run_analysis(inp)]


async def test_a_scanned_resume_reaches_the_reader_as_an_attached_pdf(model: FakeModel) -> None:
    scan = make_pdf([[]])
    events = await analyse(scan, "scan.pdf")

    assert [e for e in events if e["type"] == "error"] == []
    assert events[-1]["type"] == "done"

    # the reader call: the file is attached and the prompt says so (not "(empty)", not a placeholder)
    [call] = model.reader_calls
    assert call["pdf"] == scan
    assert "The original PDF is attached to this message: read it." in call["prompt"]
    assert "<source_content>" not in call["prompt"]
    assert re.search(r'"scanned":\s*true', call["prompt"])  # the deterministic facts mention it too

    # the source is readable (ok) all the way into the profile, and the reader's findings are used
    profile = events[-1]["profile"]
    [source] = profile["sources"]
    assert source["id"] == "resume"
    assert source["ok"] is True
    assert "error" not in source
    reader_events = [e for e in events if e["type"] == "reader"]
    assert reader_events[-1]["reader"]["status"] == "done"
    assert not any(e["type"] == "reader" and e["reader"]["status"] == "error" for e in events)

    # the synthesizer gets no resume text block for a scan (there is none), and no file
    [synth] = model.synth_calls
    assert synth["pdf"] is None
    assert "<resume_text>" not in synth["prompt"]


async def test_a_resume_with_text_is_sent_as_text_and_without_the_file(model: FakeModel) -> None:
    pdf = make_pdf([RESUME_LINES])
    events = await analyse(pdf, "cv.pdf")
    assert events[-1]["type"] == "done"
    [call] = model.reader_calls
    assert call["pdf"] is None
    assert "<source_content>" in call["prompt"]
    assert "Kitebird - built the checkout" in call["prompt"]
    [synth] = model.synth_calls
    assert "<resume_text>" in synth["prompt"]


async def test_the_pipeline_shows_the_scanned_resume_as_reading_not_as_failed(model: FakeModel) -> None:
    events = await analyse(make_pdf([[]]), "scan.pdf")
    logs = [e["text"] for e in events if e["type"] == "log"]
    assert "Little selectable text found, the reader will view the file directly" in logs
    assert not any(line.startswith("Could not read") for line in logs)
    states = [e["reader"]["status"] for e in events if e["type"] == "reader"]
    assert "reading" in states
    assert states[-1] == "done"
