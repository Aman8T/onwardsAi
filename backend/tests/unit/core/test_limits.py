"""core.limits is the single source of truth: check its shape, and that every number really is the one the
legacy code enforces (the legacy checks skip themselves once `tayyari.legacy` is deleted)."""

from __future__ import annotations

import inspect
import re
from typing import Any

import pytest

from tayyari.core import limits


def constants() -> dict[str, Any]:
    return {k: v for k, v in vars(limits).items() if k.isupper() and not k.startswith("_")}


# ----------------------------------------------------------------------------- shape


def test_every_constant_is_a_positive_number_or_a_collection() -> None:
    seen = constants()
    assert len(seen) > 100
    for name, value in seen.items():
        if isinstance(value, bool):
            raise AssertionError(f"{name} is a bool")
        if isinstance(value, int | float):
            assert value >= 0, name
            if name != "SCORE_MIN":
                assert value > 0, name
        elif isinstance(value, frozenset):
            assert value and all(isinstance(x, str) for x in value), name
        elif isinstance(value, tuple):
            assert len(value) == 2 and 0 < value[0] <= value[1], name
        else:
            raise AssertionError(f"{name} has unexpected type {type(value).__name__}")


def test_constants_are_final() -> None:
    source = inspect.getsource(limits)
    for name in constants():
        assert re.search(rf"^{name}: Final\[", source, re.MULTILINE), f"{name} is not annotated Final"


def test_relationships_between_limits() -> None:
    assert limits.MAX_ANALYZE_BODY_BYTES > limits.MAX_RESUME_BYTES
    assert limits.MAX_FIELD_BYTES >= limits.NOTES_MAX_CHARS  # a notes field must fit one form part
    assert limits.MAX_RESUME_BYTES < limits.MAX_ANALYZE_BODY_BYTES <= limits.MAX_RESUME_BYTES + limits.MIB
    assert limits.CHAT_RESUME_EXCERPT_MIN_CHARS < limits.CHAT_EXCERPT_BUDGET_CHARS
    assert limits.MAX_LINKS <= limits.LINKS_MAX_PER_REQUEST
    assert limits.RESUME_MIN_TEXT_CHARS < limits.RESUME_MAX_CHARS < limits.RESUME_MAX_EXTRACT_CHARS
    # one XML part fits in the total, and a lookup is only a part of one request
    assert limits.RESUME_MAX_DOCX_XML_BYTES <= limits.RESUME_MAX_DOCX_UNZIPPED_BYTES
    assert limits.WEB_DNS_TIMEOUT_S < limits.WEB_REQUEST_TIMEOUT_S
    assert limits.ID_MAX_CHARS <= limits.CHAT_PROFILE_ID_MAX_CHARS
    assert limits.SYNTH_NOTES_CLIP_CHARS <= limits.NOTES_MAX_CHARS


def test_prompt_guidance_never_exceeds_the_hard_caps() -> None:
    assert limits.SYNTH_DOMAINS_RANGE[1] <= limits.PROFILE_DOMAINS_MAX
    assert limits.SYNTH_SKILLS_RANGE[1] <= limits.PROFILE_SKILLS_MAX
    assert limits.SYNTH_ROLES_RANGE[1] <= limits.PROFILE_ROLES_MAX
    assert limits.SYNTH_REQUIREMENTS_RANGE[1] <= limits.ROLE_REQUIREMENTS_MAX
    assert limits.SYNTH_MIN_DOMAINS <= limits.SYNTH_DOMAINS_RANGE[0]
    assert limits.SYNTH_MIN_SKILLS <= limits.SYNTH_SKILLS_RANGE[0]
    assert limits.SYNTH_MIN_ROLES <= limits.SYNTH_ROLES_RANGE[0]
    assert limits.SKILL_EVIDENCE_MAX <= 3


def test_where_prompts_used_to_disagree_the_hard_clamp_value_won() -> None:
    assert limits.SKILL_NAME_MAX_CHARS == 24  # prompt said 22
    assert limits.ROLE_TITLE_MAX_CHARS == 30  # prompt said 26
    assert limits.READER_SKILL_EVIDENCE_MAX_CHARS == 160  # schema said 120
    assert limits.SKILL_EVIDENCE_TEXT_MAX_CHARS == 120  # schema and prompt said 90
    assert limits.ROLE_WHY_MAX_CHARS == 200  # schema and prompt said 160
    assert limits.MASTERY_INSIGHT_MAX_CHARS == 140  # schema and prompt said 110
    assert limits.READER_NOTE_MAX_CHARS == 140  # schema said 110
    assert limits.PROFILE_SOURCE_NOTE_MAX_CHARS == 140  # schema said 90
    assert limits.PROFILE_SOURCE_SUB_MAX_CHARS == 48  # schema said 40


# ----------------------------------------------------------------------------- the legacy values


def test_http_limits_match_the_legacy_server() -> None:
    server = pytest.importorskip("tayyari.legacy.server")
    assert limits.MAX_RESUME_BYTES == server.MAX_RESUME_BYTES
    assert limits.MAX_FIELD_BYTES == server.MAX_FIELD_BYTES
    assert limits.MAX_ANALYZE_BODY_BYTES == server.MAX_ANALYZE_BODY_BYTES
    assert limits.MAX_CHAT_BODY_BYTES == server.MAX_CHAT_BODY_BYTES
    assert limits.UPLOAD_EXTENSIONS == server.ACCEPTED_EXT
    assert limits.UPLOAD_MIME_TYPES == server.ACCEPTED_MIME
    assert limits.HEARTBEAT_S == server.HEARTBEAT_S
    assert limits.DISCONNECT_POLL_S == server.POLL_S

    fields = server.ChatBody.model_fields
    assert _max_length(fields["profileId"]) == limits.CHAT_PROFILE_ID_MAX_CHARS
    assert _max_length(fields["message"]) == limits.CHAT_MESSAGE_MAX_CHARS
    assert _max_length(fields["sessionId"]) == limits.CHAT_SESSION_ID_MAX_CHARS
    origin = server.ChatOriginBody.model_fields
    assert _max_length(origin["refId"]) == limits.CHAT_ORIGIN_REF_ID_MAX_CHARS
    assert _max_length(origin["title"]) == limits.CHAT_ORIGIN_TITLE_MAX_CHARS
    assert _max_length(origin["caption"]) == limits.CHAT_ORIGIN_CAPTION_MAX_CHARS


def _max_length(field: Any) -> int | None:
    for meta in field.metadata:
        if hasattr(meta, "max_length"):
            return int(meta.max_length)
    return None


def test_form_field_limits_match_the_legacy_server() -> None:
    server = pytest.importorskip("tayyari.legacy.server")
    # goal 300 / notes 20000 / link 500 / 20 links are literals inside the legacy handlers
    assert server._field("g" * 1000, limits.GOAL_MAX_CHARS) == "g" * limits.GOAL_MAX_CHARS
    parsed = server._parse_links([" ".join(f"https://x.test/{i}" + "a" * 600 for i in range(50))])
    assert len(parsed) == limits.LINKS_MAX_PER_REQUEST
    assert all(len(link) == limits.LINK_MAX_CHARS for link in parsed)
    source = inspect.getsource(server.analyze)
    assert f"{limits.NOTES_MAX_CHARS}" in source.replace("_", "")
    assert "max_files=1" in source and "max_fields=20" in source


def test_chat_limits_match_the_legacy_chat_and_claude_modules() -> None:
    chat = pytest.importorskip("tayyari.legacy.chat")
    claude = pytest.importorskip("tayyari.legacy.claude")
    assert limits.CHAT_HISTORY_MESSAGES == chat.MAX_MESSAGES
    assert limits.CHAT_MAX_PAUSE_CONTINUATIONS == claude.MAX_CONTINUATIONS
    uses = {t["name"]: t["max_uses"] for t in claude.WEB_TOOLS}
    assert uses == {"web_search": limits.WEB_SEARCH_MAX_USES, "web_fetch": limits.WEB_FETCH_MAX_USES}


def test_ingest_limits_match_the_ingest_modules() -> None:
    classify = pytest.importorskip("tayyari.ingest.classify")
    notes = pytest.importorskip("tayyari.ingest.notes")
    resume = pytest.importorskip("tayyari.ingest.resume")
    web = pytest.importorskip("tayyari.ingest.web")
    github = pytest.importorskip("tayyari.ingest.github")
    assert limits.MAX_LINKS == classify.MAX_LINKS
    assert limits.NOTES_MAX_CHARS == notes.MAX_NOTES_CHARS
    assert limits.RESUME_MAX_CHARS == resume.MAX_RESUME_CHARS
    assert limits.RESUME_MAX_EXTRACT_CHARS == resume.MAX_EXTRACT_CHARS
    assert limits.RESUME_MAX_PDF_PAGES == resume.MAX_PDF_PAGES
    assert limits.RESUME_MAX_DOCX_UNZIPPED_BYTES == resume.MAX_DOCX_UNZIPPED
    assert limits.RESUME_MAX_DOCX_XML_BYTES == resume.MAX_DOCX_XML
    assert limits.RESUME_MAX_DOCX_ELEMENTS == resume.MAX_DOCX_ELEMENTS
    assert limits.RESUME_MAX_PDF_STREAM_BYTES == resume.MAX_PDF_STREAM_BYTES
    assert limits.RESUME_MIN_TEXT_CHARS == resume.MIN_TEXT_CHARS
    assert limits.WEB_REQUEST_TIMEOUT_S == web.REQUEST_TIMEOUT
    assert limits.WEB_DNS_TIMEOUT_S == web.DNS_TIMEOUT
    assert limits.WEB_DNS_WORKERS == web.DNS_WORKERS
    assert limits.WEB_DNS_FAILURE_TTL_S == web.DNS_FAILURE_TTL
    assert limits.WEB_DNS_FAILURE_CACHE_MAX == web.DNS_FAILURE_CACHE_MAX
    assert limits.WEB_MAX_BODY_BYTES == web.MAX_BODY_BYTES
    assert limits.WEB_MAX_REDIRECTS == web.MAX_REDIRECTS
    assert limits.WEB_MAX_BUNDLES == web.MAX_BUNDLES
    assert limits.WEB_MAX_UI_STRINGS == web.MAX_UI_STRINGS
    assert limits.WEB_PAGE_TEXT_CHARS == web.PAGE_TEXT_CHARS
    assert limits.WEB_CONTENT_CHARS == web.CONTENT_CHARS
    assert limits.WEB_THIN_TEXT_CHARS == web.THIN_TEXT_CHARS
    assert limits.WEB_MAX_PAGE_LINKS == web.MAX_LINKS
    assert limits.WEB_MAX_SCRIPT_URLS == web.MAX_SCRIPT_URLS
    assert limits.WEB_MAX_HEADINGS == web.MAX_HEADINGS
    assert limits.WEB_MAX_STRUCTURED == web.MAX_STRUCTURED
    assert limits.WEB_MAX_LITERALS_SCANNED == web.MAX_LITERALS_SCANNED
    assert limits.WEB_MAX_TOKENS == web.MAX_TOKENS
    assert limits.GITHUB_MAX_CALLS == github.MAX_CALLS
    assert limits.GITHUB_README_CHARS == github.README_CHARS
    assert limits.GITHUB_README_RAW_CHARS == github.README_RAW_CHARS
    assert limits.GITHUB_DETAILED_REPOS == github.DETAILED_REPOS
    assert limits.GITHUB_README_REPOS == github.README_REPOS
    assert limits.GITHUB_TREE_REPOS == github.TREE_REPOS


# ----------------------------------------------------------------------------- the legacy clamps


def test_reader_clamps_match_the_legacy_clean_reader() -> None:
    pipeline = pytest.importorskip("tayyari.legacy.pipeline")
    word = "x" * 500
    raw = {
        "sub": word,
        "summary": "s",
        "signals": [{"label": f"l{i}{word}", "detail": word} for i in range(40)],
        "skills": [{"name": f"k{i}{word}", "evidence": word, "strength": 99} for i in range(40)],
        "notes": [f"n{i}{word}" for i in range(10)],
    }
    out = pipeline.clean_reader(raw)
    assert len(out["sub"]) == limits.READER_SUB_MAX_CHARS
    assert len(out["signals"]) == limits.READER_SIGNALS_MAX
    assert {len(s["label"]) for s in out["signals"]} == {limits.READER_SIGNAL_LABEL_MAX_CHARS}
    assert {len(s["detail"]) for s in out["signals"]} == {limits.READER_SIGNAL_DETAIL_MAX_CHARS}
    assert len(out["skills"]) == limits.READER_SKILLS_MAX
    assert {len(s["name"]) for s in out["skills"]} == {limits.READER_SKILL_NAME_MAX_CHARS}
    assert {len(s["evidence"]) for s in out["skills"]} == {limits.READER_SKILL_EVIDENCE_MAX_CHARS}
    assert {s["strength"] for s in out["skills"]} == {limits.READER_STRENGTH_MAX}
    assert len(out["notes"]) == limits.READER_NOTES_MAX
    assert {len(n) for n in out["notes"]} == {limits.READER_NOTE_MAX_CHARS}

    low = pipeline.clean_reader(
        {"skills": [{"name": "a", "evidence": "e", "strength": 0}, {"name": "b", "strength": "?"}]}
    )
    assert [s["strength"] for s in low["skills"]] == [
        limits.READER_STRENGTH_MIN,
        limits.READER_STRENGTH_DEFAULT,
    ]


def test_profile_clamps_match_the_legacy_to_profile() -> None:
    pipeline = pytest.importorskip("tayyari.legacy.pipeline")
    ingest = pytest.importorskip("tayyari.ingest")
    word = "x" * 500
    planned = ingest.PlannedSource(
        "resume", "resume", "Resume", "Resume", "resume.pdf", None, lambda log: None
    )
    raw = ingest.SourceDoc(
        id="resume", kind="resume", label="Resume", text="t", src="resume.pdf", kind_label="Resume"
    )
    reader = {"sub": "s", "summary": "sum", "signals": [], "skills": [], "notes": []}
    results = [pipeline.ReadResult(planned=planned, raw=raw, reader=reader)]
    synth = {
        "person": {"name": "Ada", "headline": word, "currentRole": word, "summary": "x"},
        "mastery": {"score": 50, "insight": word},
        "domains": [
            {"id": f"d{i}", "name": f"d{i}{word}", "short": f"s{i}{word}", "score": 50, "summary": "x"}
            for i in range(12)
        ],
        "skills": [
            {
                "id": f"s{i}",
                "name": f"s{i}{word}",
                "domainId": "d0",
                "score": 50,
                "evidence": [{"sourceId": "resume", "text": f"e{j}{word}"} for j in range(6)],
                "unlocks": [f"r{j}{word}" for j in range(6)],
            }
            for i in range(40)
        ],
        "roles": [
            {
                "id": f"r{i}",
                "title": f"r{i}{word}",
                "alignment": 90 - i,
                "eta": "~6 months",
                "why": word,
                "requirements": [
                    {"name": f"q{j}{word}", "have": False, "current": 10, "target": 70} for j in range(12)
                ],
            }
            for i in range(10)
        ],
        "sources": [{"id": "resume", "sub": word, "notes": [f"n{i}{word}" for i in range(8)]}],
    }
    profile = pipeline.to_profile(synth, results, None, False)

    assert len(profile["person"]["headline"]) == limits.PERSON_HEADLINE_MAX_CHARS
    assert len(profile["person"]["currentRole"]) == limits.PERSON_ROLE_MAX_CHARS
    assert len(profile["mastery"]["insight"]) == limits.MASTERY_INSIGHT_MAX_CHARS

    assert len(profile["domains"]) == limits.PROFILE_DOMAINS_MAX
    assert {len(d["name"]) for d in profile["domains"]} == {limits.DOMAIN_NAME_MAX_CHARS}
    assert {len(d["short"]) for d in profile["domains"]} == {limits.DOMAIN_SHORT_MAX_CHARS}

    assert len(profile["skills"]) == limits.PROFILE_SKILLS_MAX
    assert {len(s["name"]) for s in profile["skills"]} == {limits.SKILL_NAME_MAX_CHARS}
    assert {len(s["evidence"]) for s in profile["skills"]} == {limits.SKILL_EVIDENCE_MAX}
    assert {len(e["text"]) for s in profile["skills"] for e in s["evidence"]} == {
        limits.SKILL_EVIDENCE_TEXT_MAX_CHARS
    }
    assert {len(s["unlocks"]) for s in profile["skills"]} == {limits.SKILL_UNLOCKS_MAX}

    assert len(profile["roles"]) == limits.PROFILE_ROLES_MAX
    assert {len(r["title"]) for r in profile["roles"]} == {limits.ROLE_TITLE_MAX_CHARS}
    assert {len(r["why"]) for r in profile["roles"]} == {limits.ROLE_WHY_MAX_CHARS}
    assert {len(r["requirements"]) for r in profile["roles"]} == {limits.ROLE_REQUIREMENTS_MAX}
    assert {len(q["name"]) for r in profile["roles"] for q in r["requirements"]} == {
        limits.REQUIREMENT_NAME_MAX_CHARS
    }

    source = profile["sources"][0]
    assert len(source["sub"]) == limits.PROFILE_SOURCE_SUB_MAX_CHARS
    assert len(source["notes"]) == limits.PROFILE_SOURCE_NOTES_MAX
    assert {len(n) for n in source["notes"]} == {limits.PROFILE_SOURCE_NOTE_MAX_CHARS}


def test_graph_too_small_minimums_match_the_legacy_synth_check() -> None:
    pipeline = pytest.importorskip("tayyari.legacy.pipeline")
    source = inspect.getsource(pipeline._synthesize)
    assert f"len(domains) < {limits.SYNTH_MIN_DOMAINS}" in source
    assert f"len(skills) < {limits.SYNTH_MIN_SKILLS}" in source
    assert f"len(roles) < {limits.SYNTH_MIN_ROLES}" in source


def test_slug_length_matches_the_legacy_slug() -> None:
    pipeline = pytest.importorskip("tayyari.legacy.pipeline")
    assert len(pipeline.slug("x" * 200)) == limits.SLUG_MAX_CHARS


# ----------------------------------------------------------------------------- the legacy prompt budgets


def test_prompt_budgets_match_the_legacy_prompts() -> None:
    prompts = pytest.importorskip("tayyari.legacy.prompts")
    assert (
        inspect.signature(prompts.build_chat_system).parameters["budget"].default
        == limits.CHAT_EXCERPT_BUDGET_CHARS
    )

    # two sources over a 40k budget: the resume's guaranteed share is the 24k minimum (an even split is 20k)
    sources = [
        {"id": "resume", "kind": "resume", "content": "r" * 50_000},
        {"id": "g", "kind": "github", "content": "g" * 50_000},
    ]
    shares = {s["id"]: len(s["text"]) for s in prompts.budget_excerpts(sources, 40_000)}
    assert limits.CHAT_RESUME_EXCERPT_MIN_CHARS == 24_000
    assert shares["resume"] <= limits.CHAT_RESUME_EXCERPT_MIN_CHARS
    assert shares["resume"] > limits.CHAT_RESUME_EXCERPT_MIN_CHARS - 25  # `clip` keeps room for its marker

    facts = prompts.build_reader_prompt(
        {
            "id": "g",
            "kind": "github",
            "label": "GitHub",
            "kindLabel": "GitHub",
            "src": "x",
            "content": "c",
            "facts": {"k": "v" * 9000},
        }
    )
    line = next(row for row in facts.splitlines() if row.startswith("Facts gathered"))
    assert len(line.split("counts): ", 1)[1]) == limits.READER_FACTS_CLIP_CHARS + 1  # + the ellipsis

    synth = prompts.build_synth_prompt(
        goal=None,
        sources=[
            {
                "id": "a",
                "label": "A",
                "kind": "github",
                "ok": True,
                "reader": {"k": 1},
                "facts": {"k": "v" * 9000},
            }
        ],
        resume_text="r" * 40_000,
        notes_text="n" * 40_000,
    )
    resume_block = synth.split("<resume_text>\n", 1)[1].split("\n</resume_text>", 1)[0]
    notes_block = synth.split("<user_notes>\n", 1)[1].split("\n</user_notes>", 1)[0]
    assert limits.SYNTH_RESUME_CLIP_CHARS - 25 < len(resume_block) <= limits.SYNTH_RESUME_CLIP_CHARS
    assert limits.SYNTH_NOTES_CLIP_CHARS - 25 < len(notes_block) <= limits.SYNTH_NOTES_CLIP_CHARS
    facts_line = next(row for row in synth.splitlines() if row.startswith("facts: "))
    assert len(facts_line) == len("facts: ") + limits.SYNTH_READER_FACTS_CLIP_CHARS + 1
