"""`fromProfile` on POST /api/analyze: the inputs of "Update my graph from all my blocks", built in code.

`resolve_reread` runs after the form is read and before any rate-limit counter, spend reservation or slot is touched,
so a refused request costs nothing. It decides who may open the profile (the resume routes' rule: an account's own
profile; with no sign-in, an unclaimed one while anonymous matching is on; a sample run never), then builds the request
the pipeline reads exactly like an upload: the text of every stored resume file plus the person's own-words lines as one
resume source (`resume.reread`), the links and notes the profile's last analysis used, and its goal.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import Request
from pydantic import ValidationError

from tayyari.api.deps import user_or_anonymous
from tayyari.api.matching import PROFILE_NOT_FOUND
from tayyari.container import Container
from tayyari.contract.models import Profile
from tayyari.contract.requests import AnalyzeRequest, Reread
from tayyari.core.errors import NotFound
from tayyari.core.ids import is_valid_id
from tayyari.core.logging import get_logger
from tayyari.resume.errors import ResumeError
from tayyari.resume.reread import SOURCE_NAME, build_source, statements

log = get_logger(__name__)

NOTHING_TO_REREAD = "There is no resume text saved for this profile yet. Add a resume file first."


class NothingToReread(ResumeError):
    """409 `nothing_to_reread`: no stored resume file with text and no resume text in the profile's stored context."""

    status = 409
    code = "nothing_to_reread"
    user_message = NOTHING_TO_REREAD


NOT_RESUME_KINDS = frozenset({"resume", "notes"})


async def resolve_reread(request: Request, container: Container, req: AnalyzeRequest) -> AnalyzeRequest:
    ref = req.reread
    if ref is None:
        return req
    profile_id = ref.profile_id
    if not is_valid_id(profile_id):
        raise NotFound(PROFILE_NOT_FOUND)
    user = await user_or_anonymous(request, anonymous_allowed=container.match_anonymous)
    profiles = container.repos.profiles
    if user is not None:
        owned = await profiles.get_owned_for_chat(user.id, profile_id)
        if owned is None:
            raise NotFound(PROFILE_NOT_FOUND)
        doc, context = owned
    else:
        open_profile = await profiles.get_open_for_chat(profile_id)
        if open_profile is None or open_profile.is_sample:
            raise NotFound(PROFILE_NOT_FOUND)
        doc, context = open_profile.profile, open_profile.context
    try:
        profile = Profile.from_dict(doc)
    except ValidationError:
        raise NotFound(PROFILE_NOT_FOUND) from None

    texts = await container.resume.all_resume_texts(profile_id)
    said = statements(await container.repos.user_facts.list_for_profile(profile_id))
    stored = next((s.content for s in context.sources if s.kind == "resume" and s.content.strip()), None)
    source = build_source(texts, said, stored)
    if source is None:
        raise NothingToReread()

    links = tuple(dict.fromkeys(s.url for s in context.sources if s.url and s.kind not in NOT_RESUME_KINDS))
    notes = next((s.content for s in context.sources if s.kind == "notes" and s.content.strip()), None)
    log.info(
        "reread_inputs",
        profile_id=profile_id,
        files=source.files,
        own_words=source.own_words,
        links=len(links),
        duplicates=source.duplicates,
        left_out=len(source.left_out),
        from_stored_context=source.from_stored_context,
    )
    return replace(
        req,
        resume_bytes=source.text.encode("utf-8"),
        resume_name=SOURCE_NAME,
        links=links,
        goal=profile.goal,
        notes=notes,
        reread=Reread(profile_id=profile_id, user_id=None if user is None else user.id, note=source.note),
    )


__all__ = ["NOTHING_TO_REREAD", "NothingToReread", "resolve_reread"]
