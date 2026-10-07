"""Every size and count limit in one place (pure constants, nothing is computed from the environment).

Before this module the same limit lived in up to four places that drifted apart: the JSON-schema
descriptions the model reads, the prompt text, the clamp in `to_profile` / `clean_reader`, and the HTTP
layer. Schemas and prompts are now built from these constants, and the clamps use them directly.

Where a schema description and a clamp used to disagree, ONE constant holds the hard clamp value (the
value that actually shipped to the UI) and a comment records the old, lower prompt value. Prompts may
ask the model for less than the hard cap; they must never ask for more.

=================  ============================  ===================================================
Group              Names start with              Used by
=================  ============================  ===================================================
Request / HTTP     MAX_*_BYTES, MAX_FORM_*,      api/uploads, api/routes (413 and 400 answers)
                   UPLOAD_*, GOAL_/NOTES_/LINK*
Chat request       CHAT_*_MAX_CHARS              api request models (contract/requests)
Streaming          HEARTBEAT_S, DISCONNECT_*     api/ndjson
Chat behaviour     CHAT_HISTORY_*, WEB_*_USES    chat service, Claude client
Prompt budgets     CHAT_EXCERPT_*, SYNTH_*_CLIP  prompts/*
Reader output      READER_*                      analysis/normalise.clean_reader, schemas
Profile output     PROFILE_*, DOMAIN_*, SKILL_*  analysis/normalise.to_profile, schemas
                   ROLE_*, REQUIREMENT_*, ...
UI strings         HUMAN_*, READER_LINE_*, LOG_  analysis service (live log and reader lines)
Ingest             RESUME_*, WEB_*, GITHUB_*     ingest/*
Decisions          JEV_*                         decisions/packs/*, llm/jev_http
Identity           ID_MAX_CHARS, IP_HASH_*       core.ids, guard
=================  ============================  ===================================================

The small clips inside `analysis` activity lines (28 to 34 characters of a heading or path) are cosmetic
and stay next to the code that builds them.
"""

from __future__ import annotations

from typing import Final

KIB: Final[int] = 1024
MIB: Final[int] = 1024 * 1024

# ───────────────────────────── request / HTTP ─────────────────────────────

MAX_RESUME_BYTES: Final[int] = 10 * MIB
"""Largest accepted resume upload (the UI says 10 MB)."""
MAX_FIELD_BYTES: Final[int] = 256 * KIB
"""Largest single non-file form part (goal, notes, links)."""
MAX_ANALYZE_BODY_BYTES: Final[int] = MAX_RESUME_BYTES + MIB
"""Whole /api/analyze body: the resume plus the capped text fields and multipart framing."""
MAX_CHAT_BODY_BYTES: Final[int] = 1 * MIB
"""Whole /api/chat JSON body."""
MAX_UPLOAD_FILES: Final[int] = 1
MAX_FORM_FIELDS: Final[int] = 20
"""Non-file parts accepted in one multipart request."""

UPLOAD_EXTENSIONS: Final[frozenset[str]] = frozenset({".pdf", ".docx", ".txt", ".md"})
UPLOAD_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
        "text/x-markdown",
    }
)
"""Used only when the file name has no extension."""

GOAL_MAX_CHARS: Final[int] = 300
NOTES_MAX_CHARS: Final[int] = 20_000
"""Pasted notes: the form field cap and the ingest cap are the same number."""
LINK_MAX_CHARS: Final[int] = 500
LINKS_MAX_PER_REQUEST: Final[int] = 20
"""Links read from the form before classification and de-duplication."""
MAX_LINKS: Final[int] = 8
"""Usable links actually analysed (after classification and de-duplication)."""

# ───────────────────────────── chat request (ChatRequest in shared/types.ts) ─────────────────────────────

CHAT_PROFILE_ID_MAX_CHARS: Final[int] = 120
"""Request-side cap; stored ids are at most `ID_MAX_CHARS`."""
CHAT_MESSAGE_MAX_CHARS: Final[int] = 8000
CHAT_SESSION_ID_MAX_CHARS: Final[int] = 200
CHAT_ORIGIN_REF_ID_MAX_CHARS: Final[int] = 200
CHAT_ORIGIN_TITLE_MAX_CHARS: Final[int] = 200
CHAT_ORIGIN_CAPTION_MAX_CHARS: Final[int] = 300

# ───────────────────────────── streaming ─────────────────────────────

HEARTBEAT_S: Final[float] = 15.0
"""A blank line is written after this much silence so idle proxies keep the stream open; clients skip it."""
DISCONNECT_POLL_S: Final[float] = 1.0
"""How often a quiet stream checks whether the client is still there."""

# ───────────────────────────── chat behaviour ─────────────────────────────

CHAT_HISTORY_MESSAGES: Final[int] = 40
"""Messages replayed per thread (20 exchanges); older ones are dropped from the model's view."""
CHAT_TOOL_DETAIL_MAX_CHARS: Final[int] = 200
"""A tool event's `detail` (search query or URL) is cut to this length."""
WEB_SEARCH_MAX_USES: Final[int] = 5
WEB_FETCH_MAX_USES: Final[int] = 5
CHAT_MAX_PAUSE_CONTINUATIONS: Final[int] = 6
"""Times a `pause_turn` stop is continued before the turn is ended."""

# ───────────────────────────── prompt budgets ─────────────────────────────

CHAT_EXCERPT_BUDGET_CHARS: Final[int] = 60_000
"""Characters of raw source text shared by all sources in the chat system prompt (resume first)."""
CHAT_RESUME_EXCERPT_MIN_CHARS: Final[int] = 24_000
"""The resume's share of that budget is at least this much (or whatever is left)."""
CHAT_READER_SKILLS_PER_SOURCE: Final[int] = 25
"""Skills listed per reader in the chat prompt's reader notes."""
READER_FACTS_CLIP_CHARS: Final[int] = 2500
"""Deterministic `facts` JSON in a reader prompt."""
SYNTH_READER_FACTS_CLIP_CHARS: Final[int] = 1500
"""Per-source `facts` JSON in the synthesis prompt."""
SYNTH_RESUME_CLIP_CHARS: Final[int] = 15_000
SYNTH_NOTES_CLIP_CHARS: Final[int] = 3000
SYNTH_FAILED_ERROR_CLIP_CHARS: Final[int] = 80
"""Error text of an unreadable source, as listed in the synthesis prompt."""
SYNTH_REPAIR_REASON_CLIP_CHARS: Final[int] = 300
"""The previous failure quoted in the synthesis retry note."""

# Prompt guidance for the synthesizer (what it is ASKED for). The hard caps below (PROFILE_*_MAX) are
# deliberately a little higher so a slightly generous answer is trimmed instead of rejected.
SYNTH_DOMAINS_RANGE: Final[tuple[int, int]] = (4, 7)
SYNTH_SKILLS_RANGE: Final[tuple[int, int]] = (12, 26)
SYNTH_GAP_SKILLS_RANGE: Final[tuple[int, int]] = (3, 6)
SYNTH_ROLES_RANGE: Final[tuple[int, int]] = (4, 6)
SYNTH_REQUIREMENTS_RANGE: Final[tuple[int, int]] = (4, 7)
SYNTH_MIN_DOMAINS: Final[int] = 2
SYNTH_MIN_SKILLS: Final[int] = 4
SYNTH_MIN_ROLES: Final[int] = 1
"""Below any of these three minimums a synthesis result is rejected as "graph too small" and retried."""

# ───────────────────────────── reader output (clean_reader) ─────────────────────────────

READER_SUB_MAX_CHARS: Final[int] = 48
READER_SIGNALS_MAX: Final[int] = 12
READER_SIGNAL_LABEL_MAX_CHARS: Final[int] = 32
READER_SIGNAL_DETAIL_MAX_CHARS: Final[int] = 40
READER_SKILLS_MAX: Final[int] = 25
READER_SKILL_NAME_MAX_CHARS: Final[int] = 40
READER_SKILL_EVIDENCE_MAX_CHARS: Final[int] = 160
"""Hard clamp. The reader schema and prompt used to ask for 120."""
READER_NOTES_MAX: Final[int] = 4
READER_NOTE_MAX_CHARS: Final[int] = 140
"""Hard clamp. The reader schema used to ask for 110."""
READER_STRENGTH_MIN: Final[int] = 1
READER_STRENGTH_MAX: Final[int] = 5
READER_STRENGTH_DEFAULT: Final[int] = 2
"""Used when the reader's strength is missing or not a number."""

# ───────────────────────────── profile output (to_profile) ─────────────────────────────

SCORE_MIN: Final[int] = 0
SCORE_MAX: Final[int] = 100

PERSON_HEADLINE_MAX_CHARS: Final[int] = 80
PERSON_ROLE_MAX_CHARS: Final[int] = 40
MASTERY_INSIGHT_MAX_CHARS: Final[int] = 140
"""Hard clamp. The synthesis schema and prompt used to ask for 110."""

PROFILE_DOMAINS_MAX: Final[int] = 7
DOMAIN_NAME_MAX_CHARS: Final[int] = 40
DOMAIN_SHORT_MAX_CHARS: Final[int] = 16

PROFILE_SKILLS_MAX: Final[int] = 28
SKILL_NAME_MAX_CHARS: Final[int] = 24
"""Hard clamp. The synthesis schema and prompt used to ask for 22."""
SKILL_EVIDENCE_MAX: Final[int] = 3
SKILL_EVIDENCE_TEXT_MAX_CHARS: Final[int] = 120
"""Hard clamp. The synthesis schema and prompt used to ask for 90."""
SKILL_UNLOCKS_MAX: Final[int] = 3

PROFILE_ROLES_MAX: Final[int] = 6
ROLE_TITLE_MAX_CHARS: Final[int] = 30
"""Hard clamp (also applied to skill `unlocks` titles). The synthesis prompt used to ask for 26."""
ROLE_WHY_MAX_CHARS: Final[int] = 200
"""Hard clamp. The synthesis schema and prompt used to ask for 160."""
ROLE_REQUIREMENTS_MAX: Final[int] = 7
REQUIREMENT_NAME_MAX_CHARS: Final[int] = 28

PROFILE_SOURCE_SUB_MAX_CHARS: Final[int] = 48
"""Hard clamp (same as `READER_SUB_MAX_CHARS`). The synthesis schema used to ask for 40."""
PROFILE_SOURCE_NOTES_MAX: Final[int] = 4
PROFILE_SOURCE_NOTE_MAX_CHARS: Final[int] = 140
"""Hard clamp. The synthesis schema used to ask for 90."""

SLUG_MAX_CHARS: Final[int] = 40
"""Generated ids (domains, skills, roles) are cut to this length before the uniqueness suffix."""

# ───────────────────────────── UI strings (live log and reader lines) ─────────────────────────────

HUMAN_ERROR_MAX_CHARS: Final[int] = 180
"""A failure shown to the person, after the humaniser."""
READER_ERROR_LINE_MAX_CHARS: Final[int] = 64
"""The one-line reason under a failed reader or source."""
READER_LINE_MAX_CHARS: Final[int] = 70
"""A reader's current-activity line when it echoes an ingest log message."""
READER_OPENING_SRC_MAX_CHARS: Final[int] = 36
"""The source shown in "Opening <src>"."""
LOG_ERROR_DETAIL_MAX_CHARS: Final[int] = 90
LOG_NOTE_MAX_CHARS: Final[int] = 90

# ───────────────────────────── ingest ─────────────────────────────

RESUME_MAX_CHARS: Final[int] = 30_000
"""Only this many characters of a resume are analysed and kept."""
RESUME_MAX_EXTRACT_CHARS: Final[int] = 250_000
"""PDF extraction stops after this many characters."""
RESUME_MAX_PDF_PAGES: Final[int] = 20
"""Pages read from a PDF. A resume is one to three pages; the cap bounds the work a page-heavy file can cost."""
RESUME_MAX_PDF_STREAM_BYTES: Final[int] = 1 * MIB
"""One PDF stream may inflate to at most this much (a resume's content streams are a few hundred KB at most)."""
RESUME_MAX_DOCX_UNZIPPED_BYTES: Final[int] = 4 * MIB
"""A DOCX whose parts declare more than this in total is refused (zip bomb)."""
RESUME_MAX_DOCX_XML_BYTES: Final[int] = 2 * MIB
"""No single XML part of a DOCX (word/document.xml above all) may be larger than this once unpacked."""
RESUME_MAX_DOCX_ELEMENTS: Final[int] = 60_000
"""Paragraph and run start tags allowed across a DOCX's XML parts, counted before python-docx builds its tree."""
RESUME_MIN_TEXT_CHARS: Final[int] = 200
"""Below this a PDF counts as scanned and is sent to the reader as a document block."""

WEB_REQUEST_TIMEOUT_S: Final[float] = 10.0
"""One request, every redirect hop included."""
WEB_DNS_TIMEOUT_S: Final[float] = 4.0
"""One host-name lookup. The resolver thread cannot be cancelled, so the wait is abandoned after this long."""
WEB_DNS_WORKERS: Final[int] = 8
"""Threads of the pool that only resolves host names (a stuck resolver can never starve the shared default pool)."""
WEB_DNS_FAILURE_TTL_S: Final[float] = 30.0
"""A host whose lookup failed or timed out is refused straight away for this long."""
WEB_DNS_FAILURE_CACHE_MAX: Final[int] = 512
"""Failed hosts remembered at once (the oldest are dropped first)."""
WEB_MAX_BODY_BYTES: Final[int] = 2 * MIB
WEB_MAX_REDIRECTS: Final[int] = 6
WEB_MAX_BUNDLES: Final[int] = 2
WEB_MAX_UI_STRINGS: Final[int] = 250
WEB_PAGE_TEXT_CHARS: Final[int] = 25_000
WEB_CONTENT_CHARS: Final[int] = 40_000
"""Text prepared for the reader from one web page (also the ceiling for any source's text)."""
WEB_THIN_TEXT_CHARS: Final[int] = 400
"""Less visible text than this plus module scripts means a client-rendered app."""
WEB_MAX_PAGE_LINKS: Final[int] = 3000
"""Anchors collected from one page (not the user's pasted links: that is `MAX_LINKS`)."""
WEB_MAX_SCRIPT_URLS: Final[int] = 200
WEB_MAX_HEADINGS: Final[int] = 40
WEB_MAX_STRUCTURED: Final[int] = 6
WEB_MAX_LITERALS_SCANNED: Final[int] = 150_000
WEB_MAX_TOKENS: Final[int] = 400_000
"""HTML tokenizer work cap on a page that is nothing but tags."""

GITHUB_MAX_CALLS: Final[int] = 14
"""Hard budget of REST calls per profile (the unauthenticated limit is 60 per hour)."""
GITHUB_README_CHARS: Final[int] = 2500
GITHUB_README_RAW_CHARS: Final[int] = 100_000
"""Only this much of a raw README is cleaned before it is cut to `GITHUB_README_CHARS`."""
GITHUB_DETAILED_REPOS: Final[int] = 8
GITHUB_README_REPOS: Final[int] = 5
GITHUB_TREE_REPOS: Final[int] = 4

# ───────────────────────────── decisions (Jev) ─────────────────────────────
# New with the decisions framework; these come from the design notes, not from the legacy code.

JEV_FIRST_READ_RESUME_CHARS: Final[int] = 24_000
"""Redacted resume text in the first-read state."""
JEV_TRIAGE_HEAD_CHARS: Final[int] = 3500
JEV_TRIAGE_TAIL_CHARS: Final[int] = 800
JEV_EVIDENCE_WINDOW_CHARS: Final[int] = 1500
"""Best source window sent with one evidence claim."""
JEV_GATE_MESSAGE_CHARS: Final[int] = 1500
"""The user's chat message as seen by the web-search gate."""
JEV_MAX_QUESTIONS_PER_CALL: Final[int] = 30
JEV_CHOICE_DESCRIPTION_MAX_CHARS: Final[int] = 255
JEV_SCORE_LEVELS_RANGE: Final[tuple[int, int]] = (2, 10)

# ───────────────────────────── identity ─────────────────────────────

ID_MAX_CHARS: Final[int] = 81
"""Longest valid profile or session id (the legacy pattern ``[a-z0-9][a-z0-9_-]{0,80}``)."""
IP_HASH_HEX_CHARS: Final[int] = 16
"""Hex characters kept of the HMAC of a client IP (64 bits)."""
