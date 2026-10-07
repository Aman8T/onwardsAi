"""``ingest-check``: checks of the deterministic ingesters, with a compact report (port of server/scripts/ingest-check.ts).

    classify   link classification of 22 pasted strings (no network)
    terms      the skill-term dictionary on a tricky sentence and on the sample resume (no network)
    resume     TXT, PDF, DOCX (built in memory), a scanned-style PDF, a corrupt PDF, a PNG and a legacy .doc (no network)
    notes      pasted notes (no network)
    github     the live GitHub profile of gaearon             [network; ~12 of the 60 unauthenticated API calls per hour]
    web        the live page overreacted.io                   [network]
    errors     expected failures must come back as an error, never raise: a LinkedIn page, a private address
               (127.0.0.1:9, refused before any connection), a GitHub user that does not exist, a non-GitHub link
    github-repo  opt-in only: one GitHub repo URL (another ~13 API calls)

``--only a,b`` picks sections, ``--url U`` fetches one URL and ignores the sections. Each check prints ``ok`` or
``FAIL``; the exit code is 1 when any check fails. No AI call and no Anthropic or Jev key is involved. Set
``GITHUB_TOKEN`` (any token, no scopes) to avoid GitHub's unauthenticated rate limit when you run this repeatedly.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import textwrap
import time
from collections.abc import Awaitable, Sequence

from tayyari.core.logging import configure_logging
from tayyari.core.settings import Settings, get_settings
from tayyari.ingest import (
    TERM_COUNT,
    IngestLog,
    SourceDoc,
    TermHit,
    classify_url,
    count_terms,
    find_terms,
    ingest_github,
    ingest_notes,
    ingest_resume,
    ingest_web,
)

SECTIONS: tuple[str, ...] = ("classify", "terms", "resume", "notes", "github", "web", "errors")
"""The default sections, in the order they run."""
OPT_IN_SECTIONS: tuple[str, ...] = ("github-repo",)
"""Sections that run only when named in ``--only``."""

GITHUB_PROFILE_URL = "https://github.com/gaearon"
GITHUB_REPO_URL = "https://github.com/gaearon/overreacted.io"
WEB_URL = "https://overreacted.io"
LINKEDIN_URL = "https://www.linkedin.com/in/williamhgates/"
PRIVATE_URL = "http://127.0.0.1:9/"
MISSING_GITHUB_USER_URL = "https://github.com/tayyari-no-such-user-98765"
NOT_GITHUB_URL = "https://example.com/foo"

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# ───────────────────────── fixtures: built in memory, nothing is written to disk ─────────────────────────

RESUME_LINES: tuple[str, ...] = (
    "Aanya Verma",
    "Frontend Engineer | Bengaluru, India | aanya@example.com",
    "",
    "SUMMARY",
    "Frontend engineer with 3 years of experience building React and TypeScript products, now moving into AI-powered apps.",
    "",
    "EXPERIENCE",
    "Software Engineer, Orbit Labs (2023 - Present)",
    "- Built a design system in React, TypeScript and Storybook used by 5 product teams",
    "- Led the migration from Webpack to Vite, cutting CI build time by 40 percent",
    "- Mentored 2 junior engineers and ran weekly code reviews",
    "- Integrated an LLM-based search feature using OpenAI and RAG with pgvector",
    "Frontend Intern, Pixel Forge (2022)",
    "- Shipped accessible UI components with Tailwind CSS and Jest tests",
    "",
    "PROJECTS",
    "RecipeGenie - AI recipe planner built with React, Supabase and Stripe, deployed on Vercel",
    "MotionKit - open source animation library for React using Framer Motion",
    "",
    "SKILLS",
    "TypeScript, JavaScript, React, Next.js, Node.js, Python, PostgreSQL, Docker, Figma, GitHub Actions, Go",
    "",
    "EDUCATION",
    "B.Tech in Computer Science, 2022",
)

TERMS_SAMPLE = (
    "Built a RAG pipeline with LangChain and pgvector. Led a team of 4 and mentored juniors. "
    "Stack: React, React Native, TypeScript, Node.js, PostgreSQL, C++, C#, Go, Rust, SQL, NoSQL. Not Java but JavaScript. "
    "Let us go to market. Used CI/CD, A/B testing and Figma. Kubernetes (k8s)."
)

NOTES_SAMPLE = (
    "  I want to move from frontend into AI product engineering.\n\n\n"
    "Led a team of 4 at Orbit Labs; strong in React, Python and Figma.  "
)

CLASSIFY_SAMPLES: tuple[tuple[str, str | None], ...] = (
    ("github.com/gaearon", "github"),
    ("https://github.com/vercel/next.js", "github"),
    ("https://recipe-genie.lovable.app/", "lovable"),
    ("https://lovable.dev/projects/3fa73859-dba9-4c0d-b91f-883d00b23682", "lovable"),
    ("www.linkedin.com/in/some-person/", "linkedin"),
    ("https://dribbble.com/someone", "portfolio"),
    ("https://someone.github.io/site", "portfolio"),
    ("https://my-app.vercel.app", "portfolio"),
    ("https://janedoe.com/portfolio", "portfolio"),
    ("https://www.kaggle.com/someone", "link"),
    ("https://leetcode.com/u/someone/", "link"),
    ("https://medium.com/@someone", "link"),
    ("https://someone.substack.com", "link"),
    ("https://stackoverflow.com/users/1/someone", "link"),
    ("https://huggingface.co/someone", "link"),
    ("https://overreacted.io", "link"),
    ("https://example.co.uk/about", "link"),
    ("mailto:me@example.com", None),
    ("ftp://example.com/file", None),
    ("not a url", None),
    ("hello", None),
    ("", None),
)
"""(pasted text, expected kind; None when it is not a usable link)."""

# (terms the sentence must yield, terms it must not yield twice) for TERMS_SAMPLE
_TERMS_EXPECTED = (
    "React",
    "React Native",
    "TypeScript",
    "JavaScript",
    "Java",
    "LangChain",
    "RAG",
    "C++",
    "C#",
    "Go",
    "Go-to-market",
    "CI/CD",
    "A/B Testing",
    "Kubernetes",
)


def make_pdf(lines: Sequence[str]) -> bytes:
    """A one-page PDF with an uncompressed text layer (no lines: a page with no text, like a scan)."""

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(textwrap.wrap(line, width=85, break_long_words=False, break_on_hyphens=False) or [""])
    stream = ""
    if wrapped:
        stream = "BT /F1 11 Tf 50 800 Td 14 TL " + " ".join(f"({esc(w)}) Tj T*" for w in wrapped) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body = "%PDF-1.4\n"
    offsets: list[int] = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n{obj}\nendobj\n"
    xref = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    body += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    body += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return body.encode("latin-1", errors="replace")


def make_docx(paragraphs: Sequence[str]) -> bytes:
    """A DOCX with one paragraph per line, built with python-docx in memory."""
    from docx import Document

    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ───────────────────────── report ─────────────────────────


def clip(text: str, limit: int) -> str:
    """``text`` on one line, cut to ``limit`` characters."""
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 3] + "..."


class Report:
    """Prints sections, sources and ``ok`` / ``FAIL`` lines, and counts them."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.checks = 0
        self.failures: list[str] = []

    def stamp(self) -> str:
        return f"{time.monotonic() - self.started:.1f}s"

    def rule(self, title: str) -> None:
        print(f"\n== {title}")

    def logger(self, who: str) -> IngestLog:
        def log(text: str) -> None:
            print(f"  [{self.stamp()}] {who} > {text}")

        return log

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        self.checks += 1
        if ok:
            print(f"  ok   {label}")
        else:
            self.failures.append(label)
            print(f"  FAIL {label}" + (f"  -- {detail}" if detail else ""))
        return ok

    def show(self, title: str, doc: SourceDoc) -> None:
        print(f"\n--- {title}")
        print(
            f"  {'ok' if doc.ok else 'ERROR'}  id={doc.id} kind={doc.kind} label={doc.label!r} "
            f"kind_label={doc.kind_label!r} src={doc.src!r}"
        )
        if doc.error:
            print(f"  error:   {doc.error}")
        scanned = " (no text layer: the original PDF is kept for the reader)" if doc.pdf_bytes else ""
        print(f"  text:    {len(doc.text):,} chars{scanned}")
        print(f"  signals: {', '.join(doc.quick_signals) or '(none)'}")
        print(f"  meta:    {clip(json.dumps(doc.meta, ensure_ascii=False, default=str), 200)}")
        if doc.notes:
            print(f"  notes:   {clip(' | '.join(doc.notes), 200)}")
        if doc.text:
            print(f"  excerpt: {clip(doc.text, 140)}")

    async def attempt(self, title: str, pending: Awaitable[SourceDoc]) -> SourceDoc | None:
        """Await an ingester and show its result; a raised exception is a FAIL (expected failures must be returned)."""
        try:
            doc = await pending
        except Exception as exc:
            self.check(
                False, f"{title}: returned a result", f"raised {type(exc).__name__}: {clip(str(exc), 160)}"
            )
            return None
        self.show(title, doc)
        return doc

    def summary(self) -> int:
        failed = len(self.failures)
        print(f"\nDone in {self.stamp()}: {self.checks} checks, {failed} failed")
        for label in self.failures:
            print(f"  FAIL {label}")
        return 1 if failed else 0


# ───────────────────────── sections ─────────────────────────


def check_classify(report: Report) -> None:
    report.rule(f"classify ({len(CLASSIFY_SAMPLES)} samples)")
    for pasted, expected in CLASSIFY_SAMPLES:
        result = classify_url(pasted)
        got = result.kind if result else None
        shown = f"{result.kind} {result.label!r} / {result.kind_label!r} {result.url}" if result else "None"
        report.check(got == expected, f"{pasted!r:<68} -> {shown}", f"expected {expected!r}, got {got!r}")


def _counts(hits: list[TermHit]) -> dict[str, int]:
    return {hit.term: hit.count for hit in hits}


def check_terms(report: Report) -> None:
    report.rule(f"terms ({TERM_COUNT} dictionary entries)")
    found = find_terms(TERMS_SAMPLE, 40)
    print(f"  tricky sentence: {', '.join(found)}")
    counts = _counts(count_terms(TERMS_SAMPLE))
    missing = [term for term in _TERMS_EXPECTED if term not in counts]
    report.check(not missing, "the tricky sentence yields every expected term", f"missing {missing}")
    report.check(
        counts.get("Go") == 1,
        '"Go" counts once ("go to market" is not the language)',
        f"count {counts.get('Go')}",
    )
    report.check(
        counts.get("Java") == 1, '"Java" counts once and is not "JavaScript"', f"count {counts.get('Java')}"
    )
    top = find_terms("\n".join(RESUME_LINES), 8)
    print(f"  resume sample top 8: {', '.join(top)}")
    report.check(
        {"React", "TypeScript"} <= set(top),
        "the resume sample's top terms include React and TypeScript",
        f"got {top}",
    )


async def check_resume(report: Report) -> None:
    report.rule("ingest_resume (files are built in memory)")
    text = "\n".join(RESUME_LINES)

    doc = await report.attempt(
        "TXT resume", ingest_resume(text.encode("utf-8"), "aanya-resume.txt", report.logger("txt"))
    )
    if doc is not None:
        report.check(
            doc.ok and "Aanya Verma" in doc.text, "TXT: read, the name is in the text", str(doc.error)
        )
        report.check(doc.meta.get("format") == "Text", "TXT: format Text", str(doc.meta.get("format")))
        report.check("React" in doc.quick_signals, "TXT: React spotted", str(doc.quick_signals))

    doc = await report.attempt(
        "PDF resume", ingest_resume(make_pdf(RESUME_LINES), "Aanya_Verma_Resume.pdf", report.logger("pdf"))
    )
    if doc is not None:
        report.check(doc.ok and "Aanya" in doc.text, "PDF: read, the name is in the text", str(doc.error))
        report.check(
            doc.meta.get("format") == "PDF" and doc.meta.get("pages") == 1,
            "PDF: format PDF, 1 page",
            str(doc.meta),
        )
        report.check("React" in doc.quick_signals, "PDF: React spotted", str(doc.quick_signals))

    doc = await report.attempt(
        "DOCX resume", ingest_resume(make_docx(RESUME_LINES), "Aanya Verma CV.docx", report.logger("docx"))
    )
    if doc is not None:
        report.check(
            doc.ok and "Aanya Verma" in doc.text, "DOCX: read, the name is in the text", str(doc.error)
        )
        report.check(doc.meta.get("format") == "DOCX", "DOCX: format DOCX", str(doc.meta.get("format")))
        report.check("React" in doc.quick_signals, "DOCX: React spotted", str(doc.quick_signals))

    doc = await report.attempt(
        "Scanned-style PDF (no text layer)", ingest_resume(make_pdf([]), "scan.pdf", report.logger("scan"))
    )
    if doc is not None:
        report.check(
            doc.ok and doc.meta.get("scanned") is True and doc.text == "" and bool(doc.pdf_bytes),
            "scanned PDF: still readable, no text, the PDF is kept for the reader",
            f"ok={doc.ok} scanned={doc.meta.get('scanned')} text={len(doc.text)} pdf={bool(doc.pdf_bytes)}",
        )

    doc = await report.attempt(
        "Corrupt PDF", ingest_resume(b"this is not a pdf at all", "broken.pdf", report.logger("corrupt"))
    )
    if doc is not None:
        report.check(
            not doc.ok and bool(doc.error), "corrupt PDF: an error, not an exception", str(doc.error)
        )

    png = bytes([0x89, 0x50, 0x4E, 0x47, 0, 0, 0, 0])
    doc = await report.attempt(
        "Unsupported type (PNG)", ingest_resume(png, "photo.png", report.logger("png"))
    )
    if doc is not None:
        report.check(not doc.ok and bool(doc.error), "PNG: refused with an error", str(doc.error))

    doc = await report.attempt("Legacy .doc", ingest_resume(b"legacy", "old.doc", report.logger("doc")))
    if doc is not None:
        report.check(not doc.ok and bool(doc.error), ".doc: refused with an error", str(doc.error))


def check_notes(report: Report) -> None:
    report.rule("ingest_notes")
    doc = ingest_notes(NOTES_SAMPLE)
    report.show("Notes", doc)
    report.check(
        doc.ok and doc.text.startswith("I want to move"),
        "notes: leading whitespace trimmed",
        repr(doc.text[:20]),
    )
    report.check("\n\n\n" not in doc.text, "notes: runs of blank lines collapsed")
    report.check(
        {"React", "Python", "Figma"} <= set(doc.quick_signals),
        "notes: React, Python and Figma spotted",
        str(doc.quick_signals),
    )


async def _ingest_url(url: str, report: Report, title: str, *, github_token: str) -> SourceDoc | None:
    """Classify ``url`` and run the ingester for its kind (a GitHub link has its own)."""
    classified = classify_url(url)
    if classified is None:
        report.check(False, f"{title}: classified as a link", f"classify_url returned None for {url!r}")
        return None
    heading = f"{title} [{classified.kind}]"
    log = report.logger(classified.kind)
    if classified.kind == "github":
        return await report.attempt(heading, ingest_github(classified.url, log, github_token))
    return await report.attempt(
        heading, ingest_web(classified.url, classified.kind, classified.label, classified.kind_label, log)
    )


async def check_github(report: Report, github_token: str, *, repo: bool = False) -> None:
    report.rule(
        f"ingest_github({GITHUB_PROFILE_URL!r})" if not repo else f"ingest_github({GITHUB_REPO_URL!r})"
    )
    url = GITHUB_REPO_URL if repo else GITHUB_PROFILE_URL
    doc = await report.attempt(
        "GitHub repo URL" if repo else "GitHub profile",
        ingest_github(url, report.logger("github"), github_token),
    )
    if doc is None:
        return
    report.check(
        doc.ok and len(doc.text) > 200, "github: read, with real content", f"{doc.error or len(doc.text)}"
    )
    sections = [line for line in doc.text.splitlines() if line.startswith("## ")]
    print(f"  sections: {', '.join(s.removeprefix('## ') for s in sections) or '(none)'}")


async def check_web(report: Report, github_token: str) -> None:
    report.rule(f"ingest_web({WEB_URL!r})")
    doc = await _ingest_url(WEB_URL, report, "Portfolio / blog (static HTML)", github_token=github_token)
    if doc is not None:
        report.check(
            doc.ok and len(doc.text) > 200, "web: read, with real content", f"{doc.error or len(doc.text)}"
        )


async def check_errors(report: Report, github_token: str) -> None:
    report.rule("expected failures (must return an error, never raise)")
    await _ingest_url(
        LINKEDIN_URL,
        report,
        "LinkedIn (login wall: whatever it returns, it must return)",
        github_token=github_token,
    )

    doc = await _ingest_url(PRIVATE_URL, report, "Private address", github_token=github_token)
    if doc is not None:
        report.check(not doc.ok and bool(doc.error), "private address: refused with an error", str(doc.error))

    doc = await report.attempt(
        "Bad GitHub user", ingest_github(MISSING_GITHUB_USER_URL, report.logger("github"), github_token)
    )
    if doc is not None:
        report.check(not doc.ok and bool(doc.error), "unknown GitHub user: an error", str(doc.error))

    doc = await report.attempt(
        "Not a GitHub link", ingest_github(NOT_GITHUB_URL, report.logger("github"), github_token)
    )
    if doc is not None:
        report.check(not doc.ok and bool(doc.error), "non-GitHub link: an error", str(doc.error))


# ───────────────────────── entry point ─────────────────────────


def parse_only(only: str | None) -> list[str]:
    """The sections to run, in running order. ``None`` or empty: the defaults. Raises ValueError for an unknown name."""
    if only is None or not only.strip():
        return list(SECTIONS)
    wanted = {part.strip() for part in only.split(",") if part.strip()}
    unknown = sorted(wanted - set(SECTIONS) - set(OPT_IN_SECTIONS))
    if unknown:
        valid = ", ".join([*SECTIONS, *OPT_IN_SECTIONS])
        raise ValueError(f"unknown section(s): {', '.join(unknown)}. Valid sections: {valid}")
    return [name for name in (*SECTIONS, *OPT_IN_SECTIONS) if name in wanted]


async def _run(sections: list[str], url: str | None, github_token: str) -> int:
    report = Report()
    if url is not None:
        report.rule(f"custom url: {url}")
        doc = await _ingest_url(url, report, "Custom URL", github_token=github_token)
        if doc is not None:
            report.check(doc.ok, "the URL was read", str(doc.error))
        return report.summary()

    if "classify" in sections:
        check_classify(report)
    if "terms" in sections:
        check_terms(report)
    if "resume" in sections:
        await check_resume(report)
    if "notes" in sections:
        check_notes(report)
    if "github" in sections:
        await check_github(report, github_token)
    if "github-repo" in sections:
        await check_github(report, github_token, repo=True)
    if "web" in sections:
        await check_web(report, github_token)
    if "errors" in sections:
        await check_errors(report, github_token)
    return report.summary()


def run(only: str | None = None, url: str | None = None, *, settings: Settings | None = None) -> int:
    """Run the chosen sections (all by default) or just ``url``. Returns 1 when a check fails, 2 for a bad ``--only``."""
    try:
        sections = parse_only(only)
    except ValueError as exc:
        print(f"ingest-check: {exc}", file=sys.stderr)
        return 2
    base = settings if settings is not None else get_settings()
    # Library log lines would interleave with the report; warnings and errors still show.
    configure_logging(base.model_copy(update={"log_level": "WARNING"}))
    token = base.github_token.get_secret_value() if base.github_token is not None else ""
    return asyncio.run(_run(sections, url, token))
