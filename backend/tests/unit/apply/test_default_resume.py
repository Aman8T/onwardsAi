"""Apply for me sends the person's default resume, and keeps a frozen copy of it (docs/auto-apply.md "Which resume",
CLAUDE.md rule 9).

"Apply with my current resume" is the default resume's finalised file when the profile has one (the same bytes `/resume`
downloads); a default that exists only as a draft is never the file, and with no finalised default the saved upload goes as
before. The application stores the resume it was prepared with (id, title, the finalised variant, the file hash in the
digest) at the moment its values are prepared, and a later edit or a new default never changes that copy. A default that
changes between preparation and approval is caught by the digest re-check: nothing is sent, and the person reviews again.
Everything runs on fakes: the engine over the fake browser, the resume service over the fake repositories.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

from tayyari.apply.service import copy_ref
from tayyari.container import default_file_name, default_resume_of, resume_of_application
from tayyari.contract.match import ResumeVariant
from tayyari.contract.resume import FinaliseRequest, ResumeOp
from tayyari.match.export import export_resume
from tayyari.storage.ports import StoredApplication, StoredResume
from tests.unit.apply.test_engine import Env
from tests.unit.resume.support import PID, Rig, rig

ROLE = "senior-growth"
RELEVANCE: dict[str, Any] = {"scores": [{"factId": "exp-1.3", "score": 96, "requirementIds": ["r1"]}]}
SAVED = b"%PDF-1.7 cv"
"""The saved upload `Env.seed` stores."""


def container_of(r: Rig, env: Env) -> Any:
    """The parts of the container `resume_of_application` reads, over the same fakes the engine runs on."""
    return cast(
        Any, SimpleNamespace(repos=SimpleNamespace(resumes=r.repos.resumes, apply=env.repo), resume=r.service)
    )


def use_default_resume(env: Env, r: Rig) -> Any:
    box = container_of(r, env)

    async def resume_for(app: StoredApplication) -> Any:
        return await resume_of_application(box, app)

    env.engine.resume_for = resume_for
    return box


async def upload_of(r: Rig) -> StoredResume:
    """The uploaded resume: finalised and the default from the first listing."""
    [summary] = (await r.listing()).resumes
    return cast(StoredResume, await r.stored(summary.id))


async def make_path_default(r: Rig) -> StoredResume:
    """A second resume, finalised and made the default in the same call (the upload stops being it)."""
    made = await r.path(ROLE)
    answer = await r.service.finalise(
        await r.stored(made.resume.id), r.holder, FinaliseRequest(makeDefault=True)
    )
    assert answer.report.done and answer.detail.resume.isDefault
    return cast(StoredResume, await r.stored(made.resume.id))


async def pdf_of(r: Rig, stored: StoredResume) -> bytes:
    files, _ = await r.service.files(stored)
    return files.pdf


async def prepared(env: Env, r: Rig, **fields: Any) -> StoredApplication:
    """Fill the application once: its values are prepared and wait for the person's approval."""
    await env.seed(profile_id=PID, **fields)
    use_default_resume(env, r)
    assert await env.worker.run_once() is True
    app = await env.app()
    assert app.stage == "waiting_for_you" and app.review is not None
    return app


# ───────────────────────── which resume "my current resume" means ─────────────────────────


async def test_a_finalised_default_is_the_file_and_is_frozen_with_the_review() -> None:
    r = rig()
    env = Env()
    upload = await upload_of(r)
    assert upload.is_default and upload.final is not None

    app = await prepared(env, r)

    sha = hashlib.sha256(SAVED).hexdigest()  # an uploaded file nobody edited goes as it is
    name = "Riya-Kapoor.pdf"
    assert app.review is not None and app.review["resume"] == {"name": name, "sha256": sha, "choice": "saved"}
    copy = app.resume_copy
    assert copy is not None
    assert (copy["resumeId"], copy["title"], copy["sha256"], copy["fileName"]) == (
        upload.id,
        upload.title,
        sha,
        name,
    )
    assert copy["finalisedAt"] == upload.finalised_at.isoformat()  # type: ignore[union-attr]
    assert copy["variant"] == upload.final  # the finalised variant as it stood
    ref = copy_ref(app)
    assert ref is not None and (ref.resumeId, ref.sha256) == (upload.id, sha)
    assert "variant" not in ref.model_dump()  # the page is told the name, never the lines


async def test_an_edited_and_finalised_upload_sends_the_exported_pdf() -> None:
    r = rig()
    env = Env()
    upload = await upload_of(r)
    await r.service.edit(await r.stored(upload.id), [ResumeOp(op="leave_out", bulletId="b-exp-3.2")])
    assert (await r.service.finalise(await r.stored(upload.id), r.holder, FinaliseRequest())).report.done
    edited = await r.stored(upload.id)

    app = await prepared(env, r)

    exported = await pdf_of(r, edited)
    name = default_file_name(edited.facts["name"])
    assert app.review is not None and app.review["resume"]["sha256"] == hashlib.sha256(exported).hexdigest()
    assert app.review["resume"]["name"] == name and "Meera" in name
    assert app.resume_copy is not None and app.resume_copy["sha256"] == hashlib.sha256(exported).hexdigest()
    sha = hashlib.sha256(
        export_resume(ResumeVariant.model_validate(app.resume_copy["variant"])).pdf
    ).hexdigest()
    assert sha == app.resume_copy["sha256"]  # the frozen variant alone makes the very file that goes


async def test_an_unedited_upload_with_no_saved_file_falls_back_to_the_exported_pdf() -> None:
    r = rig()
    env = Env()
    upload = await upload_of(r)
    await env.seed(profile_id=PID)
    app0 = await env.app()
    await env.repo.put_resume(
        app0.owner_key, user_id=app0.user_id, name=None, media_type=None, data=None, now=app0.created_at
    )

    resume = await resume_of_application(container_of(r, env), app0)

    assert resume is not None and resume.data == await pdf_of(r, upload)


async def test_a_default_that_is_only_a_draft_is_never_the_file() -> None:
    r = rig()
    env = Env()
    upload = await upload_of(r)
    # the known-bad row: marked default, no finalised version (the routes cannot make one; the check must not trust it)
    r.repos.resumes.rows[upload.id] = replace(upload, final=None, finalised_at=None)
    box = container_of(r, env)
    await env.seed(profile_id=PID)

    assert await default_resume_of(box, PID) is None
    resume = await resume_of_application(box, await env.app())

    assert (
        resume is not None and resume.data == SAVED and resume.frozen is None
    )  # today's behaviour: the saved upload


async def test_a_draft_only_default_with_no_saved_upload_sends_no_file() -> None:
    r = rig()
    env = Env()
    upload = await upload_of(r)
    r.repos.resumes.rows[upload.id] = replace(upload, final=None, finalised_at=None)
    await env.seed(profile_id=PID)
    await env.repo.put_resume(
        "u:" + (await env.app()).user_id, user_id=(await env.app()).user_id, name=None, media_type=None, data=None,
        now=(await env.app()).created_at,
    )  # fmt: skip

    assert await resume_of_application(container_of(r, env), await env.app()) is None


async def test_no_default_resume_is_todays_behaviour() -> None:
    r = rig()  # /resume was never opened: the profile has no resumes
    env = Env()
    await env.seed(profile_id=PID)
    use_default_resume(env, r)

    assert await default_resume_of(container_of(r, env), PID) is None
    assert await env.worker.run_once() is True
    app = await env.app()

    assert app.review is not None and app.review["resume"]["name"] == "Riya-Kapoor.pdf"
    assert app.review["resume"]["sha256"] == hashlib.sha256(SAVED).hexdigest()
    assert app.resume_copy is None and copy_ref(app) is None


async def test_the_tailored_choice_never_uses_the_default() -> None:
    r = rig()
    env = Env()
    await upload_of(r)
    await env.seed(profile_id=PID, resume_choice="tailored")

    # no match here, so the tailored resume is not ready: a need, and the default is not sent in its place
    assert await resume_of_application(container_of(r, env), await env.app()) is None


# ───────────────────────── the frozen copy ─────────────────────────


async def test_a_later_edit_or_new_default_leaves_a_sent_application_as_it_was() -> None:
    r = rig(relevance=RELEVANCE)
    env = Env()
    upload = await upload_of(r)
    app = await prepared(env, r)
    await env.approve()
    assert await env.worker.run_once() is True
    sent = await env.app()
    assert sent.stage == "sent" and sent.resume_copy == app.resume_copy

    await r.service.edit(await r.stored(upload.id), [ResumeOp(op="leave_out", bulletId="b-exp-3.2")])
    await r.service.finalise(
        await r.stored(upload.id), r.holder, FinaliseRequest()
    )  # a new finalised version
    newer = await make_path_default(r)

    after = await env.app()
    assert after.resume_copy == app.resume_copy and after.resume_copy["resumeId"] == upload.id  # type: ignore[index]
    assert after.resume_copy["variant"] == upload.final  # type: ignore[index]
    assert newer.id != upload.id and after.stage == "sent"


async def test_a_new_preparation_freezes_the_resume_as_it_is_then() -> None:
    r = rig(relevance=RELEVANCE)
    env = Env()
    upload = await upload_of(r)
    first = await prepared(env, r, job={})  # the custom question is the person's: it waits for an answer
    assert first.resume_copy is not None and first.resume_copy["resumeId"] == upload.id
    newer = await make_path_default(r)

    await env.repo.update_application("ap1", {"stage": "queued"}, now=first.updated_at)
    assert await env.worker.run_once() is True
    again = await env.app()

    assert again.resume_copy is not None and again.resume_copy["resumeId"] == newer.id
    assert again.resume_copy["sha256"] == hashlib.sha256(await pdf_of(r, newer)).hexdigest()
    assert again.review is not None and again.review["resume"]["sha256"] == again.resume_copy["sha256"]


# ───────────────────────── the digest re-check ─────────────────────────


async def test_a_default_changed_after_preparation_is_caught_by_the_digest_and_nothing_is_sent() -> None:
    r = rig(relevance=RELEVANCE)
    env = Env()
    upload = await upload_of(r)
    app = await prepared(env, r)
    await env.approve()
    newer = await make_path_default(
        r
    )  # the person made another resume the default after the values were prepared

    assert await env.worker.run_once() is True
    after = await env.app()

    assert after.stage == "waiting_for_you" and after.stop_reason == "form_changed"
    assert after.approved_digest is None and after.approved_at is None and after.submitting_at is None
    assert after.review_digest not in (None, app.review_digest)
    assert not env.opener.drivers[-1].submitted and env.opener.drivers[-1].sent_writes() == []
    assert env.credits.calls == [] and after.credit_state == "held"
    # the person reviews the new file: the review and its frozen copy now name the new default, the old digest is dead
    assert after.review is not None and after.review["resume"]["sha256"] != app.review["resume"]["sha256"]  # type: ignore[index]
    assert after.resume_copy is not None and after.resume_copy["resumeId"] == newer.id != upload.id
    assert after.resume_copy["sha256"] == after.review["resume"]["sha256"]


async def test_the_same_resume_finalised_again_after_preparation_is_caught_too() -> None:
    r = rig()
    env = Env()
    upload = await upload_of(r)
    app = await prepared(env, r)
    await env.approve()
    await r.service.edit(await r.stored(upload.id), [ResumeOp(op="leave_out", bulletId="b-exp-3.2")])
    answer = await r.service.finalise(await r.stored(upload.id), r.holder, FinaliseRequest())
    assert answer.report.done

    assert await env.worker.run_once() is True
    after = await env.app()

    assert (after.stage, after.stop_reason) == ("waiting_for_you", "form_changed")
    assert not env.opener.drivers[-1].submitted and after.review_digest != app.review_digest


async def test_an_unchanged_default_is_sent_and_the_copy_is_the_file_that_went() -> None:
    r = rig()
    env = Env()
    await upload_of(r)
    app = await prepared(env, r)
    await env.approve()

    assert await env.worker.run_once() is True
    sent = await env.app()

    assert sent.stage == "sent" and env.opener.drivers[-1].submitted
    assert sent.resume_copy == app.resume_copy
    uploads = [a for a in env.opener.drivers[-1].actions if a[0] == "upload"]
    assert [a[2] for a in uploads] == [sent.resume_copy["fileName"]]  # type: ignore[index]
    assert sent.resume_copy["sha256"] == hashlib.sha256(SAVED).hexdigest()  # type: ignore[index]


async def test_a_default_changed_after_preparation_is_caught_for_an_unedited_upload_and_an_exported_one() -> (
    None
):
    # the unedited upload (original bytes) prepared, then the same resume edited and finalised: another file, caught
    r = rig(relevance=RELEVANCE)
    env = Env()
    upload = await upload_of(r)
    first = await prepared(env, r)
    assert first.review["resume"]["sha256"] == hashlib.sha256(SAVED).hexdigest()  # type: ignore[index]
    await env.approve()
    await r.service.edit(await r.stored(upload.id), [ResumeOp(op="leave_out", bulletId="b-exp-3.2")])
    assert (await r.service.finalise(await r.stored(upload.id), r.holder, FinaliseRequest())).report.done
    assert await env.worker.run_once() is True
    after = await env.app()
    assert (after.stage, after.stop_reason) == ("waiting_for_you", "form_changed")
    assert not env.opener.drivers[-1].submitted
    exported_sha = after.review["resume"]["sha256"]  # type: ignore[index]
    # now prepared with the exported PDF: a new default after approval is caught the same way
    await env.approve()
    await make_path_default(r)
    assert await env.worker.run_once() is True
    again = await env.app()
    assert (again.stage, again.stop_reason) == ("waiting_for_you", "form_changed")
    assert again.review["resume"]["sha256"] != exported_sha  # type: ignore[index]
    assert not env.opener.drivers[-1].submitted
