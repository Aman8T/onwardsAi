"""`ResumeService`: a profile's resumes, over the stored rows.

    listing        the profile's resumes and its next roles; makes the uploaded resume on the first call
    blocks         every block the person has, with how many resumes use it, its other wordings and open disagreements
    upload_file    a second resume file: its facts (the analysis's own extractor), merged into the blocks by code
                   (`resume.merge`), kept with its original bytes and made a resume of its own
    file_list      the profile's resume files; original: the bytes of the file a resume came from
    all_resume_texts  every stored file's text, newest first
    path           the resume for one next role: the one already made, else made now (one relevance call)
    detail         one resume: the draft, its marks against the uploaded resume, the facts it cites, what it leaves out
    edit           the draft with a request's operations applied (all or none)
    finalise       the typed lines checked, the person's answers applied and, with nothing left, the files exported and
                   read back; the exported variant with its lint is stored as the finalised version
    make_default   this resume becomes the profile's one default (it must have a finalised version)
    files          the PDF and DOCX of the finalised version
    grammar        proposals for the lines of a draft whose grammar, spelling or punctuation can be fixed, one Haiku call
                   (`resume.grammar`); nothing is stored, the person accepts one with the ``edit`` op
    wordings       the finalised wordings of the same blocks that the profile's other resumes use, per line (no model)
    save_match     a match's tailored resume saved into the list as a ``posting`` resume (see `resume.posting`)
    coach_view     the draft as the coach beside the page is shown it, with the check a rewrite it proposes must pass

Who may call is decided by the routes (`api/routes/resume.py`), from the resume's profile; they hand in a `Holder`.
The routes also reserve spend before `listing`, `blocks` or `path` has to make something, because making the uploaded
resume may read the sources (`MatchService.build_book`) and a path resume asks the model once for relevance.

A profile's blocks are one `FactBook`: the one the uploaded resume was made from. A path resume starts from that same
book, so a block has the same id on every resume. Every write of a draft is a compare-and-swap on the row's version.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Final

from pydantic import ValidationError

from tayyari.contract.match import Fact, ResumeVariant
from tayyari.contract.models import Profile
from tayyari.contract.resume import (
    BlockEntry,
    BlocksResponse,
    FinaliseReport,
    FinaliseRequest,
    FinaliseResponse,
    GrammarResponse,
    PathSlot,
    ResumeBlock,
    ResumeDetail,
    ResumeFilesResponse,
    ResumeList,
    ResumeOp,
    ResumeState,
    ResumeSummary,
    UploadResponse,
    WordingsResponse,
)
from tayyari.contract.stored import StoredContext, StoredSource
from tayyari.core import limits
from tayyari.core.clock import Clock
from tayyari.core.files import stored_media_type
from tayyari.core.logging import get_logger
from tayyari.ingest.resume import ResumeError as IngestError
from tayyari.ingest.resume import parse_resume
from tayyari.llm.claude import ClaudeClient
from tayyari.llm.types import CallContext
from tayyari.match.calls import call_timeout
from tayyari.match.export import ExportedFiles, plain
from tayyari.match.facts import build_facts, extract_portfolio_facts
from tayyari.match.models import FactBook
from tayyari.match.service import MatchService
from tayyari.resume.arrange import (
    BULLET_PREFIX,
    block_ids,
    entry_of_block,
    path_variant,
    score_role,
    upload_variant,
)
from tayyari.resume.coach import CoachView, coach_view
from tayyari.resume.errors import (
    NoOriginal,
    NotFinalised,
    ResumeChanged,
    ResumeNotFound,
    RoleNotFound,
    TooManyFiles,
    UnreadableFile,
)
from tayyari.resume.files import (
    ResumeText,
    clean_filename,
    decorations,
    file_title,
    listing,
    merged_book,
    report_of,
)
from tayyari.resume.finalise import finalise
from tayyari.resume.grammar import propose
from tayyari.resume.marks import changes, cited_facts, cited_ids, left_out, marks, used_blocks
from tayyari.resume.merge import merge
from tayyari.resume.ops import apply_ops, entries_of
from tayyari.resume.posting import as_posting_resume
from tayyari.resume.wordings import WordingSource, offered
from tayyari.storage.ports import ResumeFileRepo, ResumeRepo, StoredResume, StoredResumeFile, UserFactRepo

log = get_logger(__name__)

UPLOAD_TITLE: Final = "Your resume"
"""The uploaded resume's title when the profile's resume source has no label."""
MINE_QUESTION: Final = "A line of your resume, in your own words"
"""What a ``mine`` fact answers, as `user_facts` records it."""
MINE_KEY: Final = "resume:"
"""A ``mine`` fact's reference names the resume and the line it was typed on: ``resume:<id>/<line id>``."""
ROLE_KEY_MAX: Final = 64


@dataclass(frozen=True, slots=True)
class Holder:
    """Whose blocks a request is about: the profile, the account that owns it (None for a profile with no account) and
    whether what the person says may be kept for the profile (False on a sample run, which everyone who holds its id
    shares: there it stays on the resume it was typed on)."""

    profile_id: str
    user_id: str | None
    shares_facts: bool = True


@dataclass(frozen=True, slots=True)
class OriginalFile:
    """The bytes a resume came from, as they were uploaded."""

    data: bytes
    mime: str
    filename: str


@dataclass(frozen=True, slots=True)
class _Opened:
    """A stored resume, validated."""

    stored: StoredResume
    draft: ResumeVariant
    final: ResumeVariant | None
    book: FactBook


def book_json(book: FactBook) -> dict[str, Any]:
    """The `FactBook` as the JSON a resume row keeps (as a match row does)."""
    return book.model_dump(mode="json", exclude_none=True)


def _without_lint(variant: ResumeVariant) -> ResumeVariant:
    return variant.model_copy(update={"lint": None})


def same_arrangement(one: ResumeVariant, other: ResumeVariant) -> bool:
    """Do two variants say the same thing (their checklists aside)?"""
    return _without_lint(one) == _without_lint(other)


def export_notes(variant: ResumeVariant) -> list[str]:
    """What the export changed by itself, in plain words: characters only, never wording."""
    lines = [
        line.text for entry in entries_of(variant) for line in entry.bullets if line.status == "verified"
    ]
    folded = sum(1 for text in lines if plain(text) != text)
    if not folded:
        return []
    return [
        "Wrote special characters (curly quotes, long dashes, symbols a PDF cannot show) in plain form "
        f"on {folded} line{'s' if folded != 1 else ''}."
    ]


def upload_title(profile: Profile) -> str:
    label = next((s.label.strip() for s in profile.sources if s.kind == "resume" and s.label.strip()), "")
    return label or UPLOAD_TITLE


def _own_words(fact: Fact) -> bool:
    """Is ``fact`` a line the person typed on a resume and vouched for (``mine``)?"""
    return bool(fact.refs) and all(r.source == "user" and r.docId.startswith(MINE_KEY) for r in fact.refs)


def _typed_on(fact: Fact) -> str | None:
    """The block the line of a ``mine`` fact was written from (``resume:<id>/b-<block id>``), when its reference says."""
    line = fact.refs[0].docId.partition("/")[2] if fact.refs else ""
    return line.removeprefix(BULLET_PREFIX) if line.startswith(BULLET_PREFIX) else None


class ResumeService:
    """See the module docstring. ``model`` scores relevance for a path resume (the match's reading model)."""

    def __init__(
        self,
        *,
        claude: ClaudeClient,
        model: str,
        match: MatchService,
        resumes: ResumeRepo,
        user_facts: UserFactRepo,
        clock: Clock,
        call_timeout_s: float | None = None,
        files: ResumeFileRepo | None = None,
    ) -> None:
        self._claude = claude
        self._model = model
        self._match = match
        self._resumes = resumes
        self._user_facts = user_facts
        self._clock = clock
        self._timeout = call_timeout_s
        self._files = files
        self._locks: dict[str, asyncio.Lock] = {}

    # ── reading rows ──

    async def get(self, resume_id: str) -> StoredResume | None:
        """The stored resume with this id, or None. The caller decides who may see it."""
        return await self._resumes.get(resume_id)

    async def find_upload(self, profile_id: str) -> StoredResume | None:
        return await self._resumes.find(profile_id, "upload", "")

    async def find_path(self, profile_id: str, role_id: str) -> StoredResume | None:
        if not role_id or len(role_id) > ROLE_KEY_MAX:
            return None
        return await self._resumes.find(profile_id, "path", role_id)

    def _open(self, stored: StoredResume) -> _Opened:
        try:
            return _Opened(
                stored=stored,
                draft=ResumeVariant.model_validate(stored.draft),
                final=None if stored.final is None else ResumeVariant.model_validate(stored.final),
                book=FactBook.model_validate(stored.facts),
            )
        except ValidationError:
            # Stored in a shape this build cannot read (the contract moved on): it is not served half-understood.
            log.error("resume_unreadable", resume_id=stored.id, version=stored.version)
            raise ResumeNotFound() from None

    def _readable(self, rows: Sequence[StoredResume]) -> list[_Opened]:
        out: list[_Opened] = []
        for row in rows:
            try:
                out.append(self._open(row))
            except ResumeNotFound:
                continue
        return out

    # ── what the page shows ──

    def _summary(self, opened: _Opened, upload: ResumeVariant) -> ResumeSummary:
        stored = opened.stored
        state: ResumeState = (
            "final" if opened.final is not None and same_arrangement(opened.draft, opened.final) else "draft"
        )
        return ResumeSummary(
            id=stored.id,
            kind=stored.kind,
            title=stored.title,
            roleId=stored.role_key if stored.kind == "path" else None,
            matchId=stored.role_key if stored.kind == "posting" else None,
            state=state,
            isDefault=stored.is_default,
            changes=changes(opened.draft, upload),
            updatedAt=stored.updated_at.isoformat(),
            finalisedAt=None if stored.finalised_at is None else stored.finalised_at.isoformat(),
        )

    def _detail(self, opened: _Opened) -> ResumeDetail:
        upload = upload_variant(opened.book)
        return ResumeDetail(
            resume=self._summary(opened, upload),
            variant=opened.draft,
            marks=marks(opened.draft, upload),
            facts=cited_facts(opened.draft, opened.book),
            leftOut=left_out(opened.draft, opened.book),
            canDownload=opened.final is not None,
        )

    def detail(self, stored: StoredResume) -> ResumeDetail:
        """One resume as the page shows it. Raises `ResumeNotFound` for a row this build cannot read."""
        return self._detail(self._open(stored))

    # ── making resumes ──

    async def _export(self, variant: ResumeVariant, book: FactBook) -> ResumeVariant | None:
        """``variant`` with its checklist once its files are made and read back, or None when that failed."""
        try:
            return (await self._match.export_resume(variant, book)).variant
        except Exception:
            log.warning("resume_export_failed", exc_info=True)
            return None

    async def ensure_upload(
        self, holder: Holder, profile: Profile, context: StoredContext, call_ctx: CallContext
    ) -> StoredResume:
        """The profile's uploaded resume: the stored one, else made now from the profile's fact book, finalised at once
        (every line is its own source's words) and marked the default."""
        existing = await self.find_upload(holder.profile_id)
        if existing is not None:
            return existing
        said = await self._user_facts.list_for_profile(holder.profile_id) if holder.shares_facts else []
        with call_timeout(self._timeout):
            book = await self._match.build_book(profile, context, call_ctx=call_ctx, user_facts=said)
        variant = upload_variant(book)
        final = await self._export(variant, book)
        now = self._clock.now()
        stored = await self._resumes.create(
            StoredResume(
                id=secrets.token_hex(16),
                user_id=holder.user_id,
                profile_id=holder.profile_id,
                kind="upload",
                role_key="",
                title=upload_title(profile),
                draft=(final or variant).to_dict(),
                final=None if final is None else final.to_dict(),
                facts=book_json(book),
                is_default=True,
                version=1,
                created_at=now,
                updated_at=now,
                finalised_at=None if final is None else now,
            )
        )
        log.info(
            "resume_upload_made",
            resume_id=stored.id,
            facts=len(book.facts),
            lines=sum(len(e.bullets) for e in entries_of(variant)),
            finalised=final is not None,
        )
        return stored

    async def _rows(self, holder: Holder) -> list[StoredResume]:
        """The profile's rows, given to the account that has since claimed the profile when they had none."""
        rows = await self._resumes.list_for_profile(holder.profile_id)
        if holder.user_id is not None and any(r.user_id is None for r in rows):
            await self._resumes.adopt(holder.profile_id, holder.user_id)
            if self._files is not None:
                await self._files.adopt(holder.profile_id, holder.user_id)
            rows = [replace(r, user_id=holder.user_id) if r.user_id is None else r for r in rows]
        return rows

    async def listing(
        self, holder: Holder, profile: Profile, context: StoredContext, call_ctx: CallContext
    ) -> ResumeList:
        """The profile's resumes (the uploaded one first) and its next roles, each with the resume made for it."""
        upload = self._open(await self.ensure_upload(holder, profile, context, call_ctx))
        opened = self._readable(await self._rows(holder))
        files = await self._file_rows(holder.profile_id)
        by_role = {o.stored.role_key: o.stored.id for o in opened if o.stored.kind == "path"}
        ordered = sorted(opened, key=lambda o: o.stored.kind != "upload")
        return ResumeList(
            profileId=holder.profile_id,
            resumes=[self._summary(o, upload_variant(o.book)) for o in ordered],
            paths=[
                PathSlot(roleId=r.id, title=r.title, alignment=r.alignment, resumeId=by_role.get(r.id))
                for r in profile.roles
            ],
            blocks=len(self._blocks(merged_book(upload.book, files), opened, files)),
        )

    async def _file_rows(self, profile_id: str) -> list[StoredResumeFile]:
        return [] if self._files is None else await self._files.list_for_profile(profile_id)

    def _blocks(
        self, book: FactBook, opened: Sequence[_Opened], files: Sequence[StoredResumeFile] = ()
    ) -> list[ResumeBlock]:
        """One block per job, project and degree, one per bullet under it, and one per line the person typed and
        vouched for; each with how many of the resumes use it, the other wordings files gave it and what they disagree on."""
        wordings, clashes = decorations(files, [o.stored for o in opened])

        def extra(fact_id: str) -> dict[str, Any]:
            return {
                "wordings": wordings.get(fact_id) or None,
                "disagreements": clashes.get(fact_id) or None,
            }

        facts = book.by_id()
        placed = [
            ({e.entryFactId for e in entries_of(o.draft)} | {e.factId for e in o.draft.education})
            for o in opened
        ]
        written = [used_blocks(o.draft, o.book) for o in opened]
        named = [cited_ids(o.draft) for o in opened]
        own: dict[str, Fact] = {}
        for o in opened:
            for fact in o.book.facts:
                if _own_words(fact):
                    own.setdefault(fact.id, fact)
        own_by_entry: dict[str | None, list[Fact]] = {}
        for fact in own.values():
            home = entry_of_block(book, _typed_on(fact) or "")
            own_by_entry.setdefault(home.factId if home is not None else None, []).append(fact)

        def said(entry_id: str | None) -> list[ResumeBlock]:
            return [
                ResumeBlock(
                    fact=f, entryFactId=entry_id, usedIn=sum(1 for ids in named if f.id in ids), **extra(f.id)
                )
                for f in own_by_entry.get(entry_id, [])
            ]

        out: list[ResumeBlock] = []
        for entry in book.entries:
            head = facts.get(entry.factId)
            if head is None:
                continue
            out.append(
                ResumeBlock(
                    fact=head, usedIn=sum(1 for ids in placed if entry.factId in ids), **extra(entry.factId)
                )
            )
            for fid in block_ids(entry):
                if fid == entry.factId or fid not in facts:
                    continue
                out.append(
                    ResumeBlock(
                        fact=facts[fid],
                        entryFactId=entry.factId,
                        usedIn=sum(1 for ids in written if fid in ids),
                        **extra(fid),
                    )
                )
            out.extend(said(entry.factId))
        out.extend(said(None))
        return out

    async def blocks(
        self, holder: Holder, profile: Profile, context: StoredContext, call_ctx: CallContext
    ) -> BlocksResponse:
        """Every block the person has (see `_blocks`), from the fact book the uploaded resume was made from."""
        upload = self._open(await self.ensure_upload(holder, profile, context, call_ctx))
        opened = self._readable(await self._rows(holder))
        files = await self._file_rows(holder.profile_id)
        book = merged_book(upload.book, files)
        return BlocksResponse(
            profileId=holder.profile_id,
            entries=[
                BlockEntry(factId=e.factId, kind=e.kind, title=e.title, org=e.org, dates=e.dates)
                for e in book.entries
            ],
            blocks=self._blocks(book, opened, files),
            resumes=len(opened),
        )

    async def path(
        self,
        holder: Holder,
        profile: Profile,
        context: StoredContext,
        role_id: str,
        call_ctx: CallContext,
    ) -> ResumeDetail:
        """The resume for the next role ``role_id``: the one already made, else arranged now from the profile's blocks
        (one relevance call, the lexical score when the model cannot be asked). Raises `RoleNotFound` for a role that
        is not among the profile's."""
        role = next((r for r in profile.roles if r.id == role_id), None)
        if role is None or len(role.id) > ROLE_KEY_MAX:
            raise RoleNotFound()
        existing = await self.find_path(holder.profile_id, role.id)
        if existing is not None:
            return self.detail(existing)
        base = self._open(await self.ensure_upload(holder, profile, context, call_ctx)).book
        book = merged_book(base, await self._file_rows(holder.profile_id))
        if holder.shares_facts:
            book = book.with_facts(await self._user_facts.list_for_profile(holder.profile_id))
        with call_timeout(self._timeout):
            relevance = await score_role(
                self._claude, model=self._model, book=book, role=role, call_ctx=call_ctx
            )
        variant = path_variant(book, role, relevance, skills=profile.skills)
        now = self._clock.now()
        stored = await self._resumes.create(
            StoredResume(
                id=secrets.token_hex(16),
                user_id=holder.user_id,
                profile_id=holder.profile_id,
                kind="path",
                role_key=role.id,
                title=role.title,
                draft=variant.to_dict(),
                final=None,
                facts=book_json(book),
                is_default=False,
                version=1,
                created_at=now,
                updated_at=now,
                finalised_at=None,
            )
        )
        log.info(
            "resume_path_made",
            resume_id=stored.id,
            scored_by_model=relevance.from_model,
            lines=sum(len(e.bullets) for e in entries_of(variant)),
        )
        return self.detail(stored)

    # ── changing a resume ──

    async def _write(
        self,
        stored: StoredResume,
        *,
        draft: ResumeVariant,
        book: FactBook | None = None,
        final: ResumeVariant | None = None,
        finalised_at: datetime | None = None,
    ) -> StoredResume:
        """Store a new draft (and, with ``final``, a new finalised version) on the version ``stored`` was read at.
        Raises `ResumeChanged` when another request wrote first."""
        item = replace(
            stored,
            draft=draft.to_dict(),
            facts=stored.facts if book is None else book_json(book),
            final=stored.final if final is None else final.to_dict(),
            finalised_at=stored.finalised_at if final is None else finalised_at,
            updated_at=self._clock.now(),
            version=stored.version + 1,
        )
        if not await self._resumes.save(item, expected_version=stored.version):
            raise ResumeChanged()
        return item

    async def edit(self, stored: StoredResume, ops: Sequence[ResumeOp]) -> ResumeDetail:
        """The draft with ``ops`` applied, all or none (`OpRefused` names the first that does not fit)."""
        opened = self._open(stored)
        if not ops:
            return self._detail(opened)
        draft = apply_ops(opened.draft, opened.book, ops)
        if opened.final is not None and same_arrangement(draft, opened.final):
            draft = (
                opened.final
            )  # edited back to exactly what was finalised: its checklist still describes it
        return self.detail(await self._write(stored, draft=draft))

    async def finalise(
        self,
        stored: StoredResume,
        holder: Holder,
        request: FinaliseRequest,
        *,
        terms: Sequence[str] = (),
    ) -> FinaliseResponse:
        """Check the typed lines, apply the person's answers and, with nothing left, export the files and store the
        exported variant with its lint as the finalised version (see `resume.finalise`). ``terms`` are the words of the
        role's requirements for a path resume. A line the person calls their own is kept as a user fact of the profile,
        so later resumes and matches can cite it."""
        opened = self._open(stored)
        outcome = finalise(
            opened.draft, opened.book, request.answers or (), terms=terms, key=f"{MINE_KEY}{stored.id}"
        )
        saved = stored
        if outcome.done:
            exported = await self._match.export_resume(outcome.variant, outcome.book)
            final = exported.variant
            saved = await self._write(
                stored, draft=final, book=outcome.book, final=final, finalised_at=self._clock.now()
            )
            if request.makeDefault and await self._resumes.set_default(saved.profile_id, saved.id):
                saved = replace(saved, is_default=True)
            report = FinaliseReport(done=True, findings=[], fixed=export_notes(final), lint=final.lint)
        else:
            if outcome.new_facts or not same_arrangement(outcome.variant, opened.draft):
                saved = await self._write(stored, draft=outcome.variant, book=outcome.book)
            report = FinaliseReport(done=False, findings=outcome.findings, fixed=[], lint=None)
        if holder.shares_facts:
            for fact in outcome.new_facts:
                await self._user_facts.add(
                    holder.user_id,
                    stored.profile_id,
                    fact,
                    question=MINE_QUESTION,
                    answer=fact.refs[0].quote if fact.refs else fact.text,
                    match_id=None,
                )
        return FinaliseResponse(detail=self.detail(saved), report=report)

    async def grammar(
        self, stored: StoredResume, *, terms: Sequence[str] = (), call_ctx: CallContext
    ) -> GrammarResponse:
        """The grammar fixes for the draft that pass the guard (`resume.grammar`): one Haiku call, thinking off. Nothing is
        stored. ``terms`` are the role's requirement words for a path resume (a fix may not bring one in). With no key, or
        when the call fails, there are no proposals and ``ran`` is False; only the daily spend ceiling propagates."""
        opened = self._open(stored)
        with call_timeout(self._timeout):
            result = await propose(
                self._claude, model=self._model, variant=opened.draft, terms=terms, call_ctx=call_ctx
            )
        return GrammarResponse(proposals=result.proposals, ran=result.ran)

    def coach_view(self, stored: StoredResume, terms: Sequence[str] = ()) -> CoachView:
        """The draft as the coach is shown it (see `resume.coach`). The caller has decided that the person may open this
        resume. Raises `ResumeNotFound` for a row this build cannot read."""
        opened = self._open(stored)
        return coach_view(stored.id, stored.title, opened.draft, opened.book, terms)

    async def wordings(self, stored: StoredResume) -> WordingsResponse:
        """The finalised wordings of the same blocks that the profile's other resumes use, for each line of this draft
        (see `resume.wordings`). Pure code over the stored resumes: no model call."""
        opened = self._open(stored)
        others = [
            o
            for o in self._readable(await self._resumes.list_for_profile(stored.profile_id))
            if o.stored.id != stored.id and o.final is not None
        ]
        others.sort(
            key=lambda o: (o.stored.finalised_at or o.stored.updated_at, o.stored.id), reverse=True
        )  # newest finalised first
        sources = [
            WordingSource(o.stored.id, o.stored.title, o.final, o.book) for o in others if o.final is not None
        ]
        return WordingsResponse(wordings=offered(opened.draft, opened.book, sources))

    async def save_match(
        self, holder: Holder, *, match_id: str, title: str, variant: ResumeVariant, book: FactBook
    ) -> ResumeDetail:
        """Save a match's tailored resume into the profile's list as a ``posting`` resume (role key: the match id), with
        the facts as they stood. Already finalised when the match had exported it. Saving the same match again returns the
        resume saved the first time."""
        if not match_id or len(match_id) > ROLE_KEY_MAX:
            raise ResumeNotFound()
        existing = await self._resumes.find(holder.profile_id, "posting", match_id)
        if existing is not None:
            return self.detail(existing)
        draft, finalised = as_posting_resume(variant, book)
        now = self._clock.now()
        stored = await self._resumes.create(
            StoredResume(
                id=secrets.token_hex(16),
                user_id=holder.user_id,
                profile_id=holder.profile_id,
                kind="posting",
                role_key=match_id,
                title=(" ".join(title.split()) or "Posting resume")[:255],
                draft=draft.to_dict(),
                final=draft.to_dict() if finalised else None,
                facts=book_json(book),
                is_default=False,
                version=1,
                created_at=now,
                updated_at=now,
                finalised_at=now if finalised else None,
            )
        )
        log.info(
            "resume_posting_saved",
            resume_id=stored.id,
            lines=sum(len(e.bullets) for e in entries_of(draft)),
            finalised=finalised,
        )
        return self.detail(stored)

    # ── a second file ──

    def _lock(self, profile_id: str) -> asyncio.Lock:
        return self._locks.setdefault(profile_id, asyncio.Lock())

    async def upload_file(
        self,
        holder: Holder,
        profile: Profile,
        context: StoredContext,
        *,
        filename: str,
        content_type: str | None,
        data: bytes,
        call_ctx: CallContext,
    ) -> UploadResponse:
        """Merge a resume file into the profile's blocks and keep it as a resume of its own.

        The file is read by the existing resume reader (`ingest.resume.parse_resume`), its facts come from the analysis's own
        extractor (`match.facts`: the portfolio's line-cited extraction, one Haiku call, the file's text fenced as data, every
        quote checked against the file by code), and `resume.merge` places each fact by code. The file's resume is
        `upload_variant` over the file's facts under the block ids they were placed on, finalised at once like the first one,
        and never the default. The same bytes uploaded twice answer with the first upload's result and no model call. The
        caller reserves spend first (one extraction, two when the profile has no uploaded resume yet).
        Raises `UnreadableFile`, `TooManyFiles`."""
        if self._files is None:
            raise RuntimeError("resume files are not wired")
        name = clean_filename(filename)
        digest = hashlib.sha256(data).hexdigest()
        async with self._lock(holder.profile_id):
            rows = await self._files.list_for_profile(holder.profile_id)
            resumes = await self._resumes.list_for_profile(holder.profile_id)
            for row in rows:
                report = report_of(row)
                made = next((r for r in resumes if r.kind == "upload" and r.role_key == row.id), None)
                if row.sha256 == digest and report is not None and made is not None:
                    return UploadResponse(resume=self.detail(made), merge=report)
            if len(rows) >= limits.MAX_RESUME_FILES_PER_PROFILE:
                raise TooManyFiles()
            text = await self._read(data, name)
            base = self._open(await self.ensure_upload(holder, profile, context, call_ctx)).book
            current = merged_book(base, rows)
            file_id = secrets.token_hex(16)
            source = StoredContext(
                sources=[StoredSource(id=file_id, label=name, kind="resume", content=text)]
            )
            with call_timeout(self._timeout):
                extracted = await extract_portfolio_facts(
                    self._claude,
                    model=self._model,
                    context=source,
                    profile=profile,
                    call_ctx=replace(call_ctx, purpose="match.facts"),
                )
            found = await asyncio.to_thread(build_facts, profile, source, extracted.facts)
            if not found.facts:
                raise UnreadableFile("We could not find any experience, projects or education in that file.")
            result = merge(current, found, file_id=file_id, filename=name)
            variant = upload_variant(result.file_book)
            final = await self._export(variant, result.file_book)
            now = self._clock.now()
            stored = await self._resumes.create(
                StoredResume(
                    id=secrets.token_hex(16),
                    user_id=holder.user_id,
                    profile_id=holder.profile_id,
                    kind="upload",
                    role_key=file_id,
                    title=file_title(name),
                    draft=(final or variant).to_dict(),
                    final=None if final is None else final.to_dict(),
                    facts=book_json(result.file_book),
                    is_default=False,
                    version=1,
                    created_at=now,
                    updated_at=now,
                    finalised_at=None if final is None else now,
                )
            )
            await self._files.add(
                StoredResumeFile(
                    id=file_id,
                    user_id=holder.user_id,
                    profile_id=holder.profile_id,
                    origin="upload",
                    filename=name,
                    mime=stored_media_type(name, content_type, data),
                    size=len(data),
                    sha256=digest,
                    text=text,
                    facts=book_json(found),
                    merge=result.report.to_dict(),
                    merged_book=book_json(result.merged),
                    created_at=now,
                ),
                data,
            )
        report = result.report
        log.info(
            "resume_file_merged",
            file_id=file_id,
            same=len(report.same),
            new=len(report.new),
            near=len(report.near),
            disagree=len(report.disagree),
        )
        return UploadResponse(resume=self.detail(stored), merge=report)

    async def _read(self, data: bytes, name: str) -> str:
        """The text of a resume file (the analysis's own reader), or `UnreadableFile` with the reason in words."""
        try:
            text = (await parse_resume(data, name)).replace("\x00", "")
        except IngestError as exc:
            raise UnreadableFile(str(exc)) from None
        text = text[: limits.RESUME_MAX_CHARS]
        if len(text.strip()) < limits.RESUME_MIN_TEXT_CHARS:
            raise UnreadableFile(
                "No readable text was found in that file. A scanned PDF cannot be read: export a text version."
            )
        return text

    async def file_list(self, holder: Holder) -> ResumeFilesResponse:
        """The profile's resume files, oldest first: the one it was analysed from, then each upload."""
        files = await self._file_rows(holder.profile_id)
        resumes = await self._resumes.list_for_profile(holder.profile_id)
        return ResumeFilesResponse(profileId=holder.profile_id, files=listing(files, resumes))

    async def original(self, stored: StoredResume) -> OriginalFile:
        """The bytes the resume came from. Raises `NoOriginal` for a path or posting resume, and for the first resume of a
        profile analysed before originals were kept."""
        if self._files is None or stored.kind != "upload":
            raise NoOriginal()
        rows = await self._files.list_for_profile(stored.profile_id)
        row = next(
            (
                r
                for r in rows
                if (r.origin == "analysis" if stored.role_key == "" else r.id == stored.role_key)
            ),
            None,
        )
        data = None if row is None else await self._files.get_data(row.id)
        if row is None or data is None:
            raise NoOriginal()
        return OriginalFile(data=data, mime=row.mime, filename=row.filename)

    async def all_resume_texts(self, profile_id: str) -> list[ResumeText]:
        """The text of every resume file stored for the profile with its file name and date, newest first (empty for a
        profile analysed before files were kept: its one resume text is in the profile's stored context)."""
        rows = await self._file_rows(profile_id)
        ordered = sorted(rows, key=lambda r: (r.created_at, r.id), reverse=True)
        return [
            ResumeText(
                file_id=r.id, filename=r.filename, text=r.text, created_at=r.created_at, origin=r.origin
            )
            for r in ordered
        ]

    async def make_default(self, stored: StoredResume) -> ResumeDetail:
        """Make this resume the profile's default. Raises `NotFinalised` unless it has a finalised version."""
        if stored.final is None or not await self._resumes.set_default(stored.profile_id, stored.id):
            raise NotFinalised()
        return self.detail(replace(stored, is_default=True))

    async def files(self, stored: StoredResume) -> tuple[ExportedFiles, str]:
        """The files of the finalised version and the person's name (for the file name). Raises `NotFinalised` while
        there is none: a draft is never downloaded."""
        opened = self._open(stored)
        if opened.final is None:
            raise NotFinalised()
        exported = await self._match.export_resume(opened.final, opened.book)
        return exported.files, opened.book.name


__all__ = [
    "MINE_KEY",
    "MINE_QUESTION",
    "UPLOAD_TITLE",
    "Holder",
    "OriginalFile",
    "ResumeService",
    "book_json",
    "export_notes",
    "same_arrangement",
    "upload_title",
]
