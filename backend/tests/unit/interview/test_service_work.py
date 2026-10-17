"""A work sample, then defend it: the task from a template (no phrasing call), the optional assistant, the work handed in, three
questions about it (the third only when the assistant was used), and a debrief whose checks quote the work."""

from __future__ import annotations

from typing import Any

import pytest

from tayyari.contract.interview import AssistantRequest, StartRequest, WorkSubmit
from tayyari.core.errors import BadRequest
from tayyari.core.settings import InterviewSettings
from tayyari.interview.errors import AssistantUnavailable, BadPlan, ModelUnavailable, NoTemplate, WorkFirst
from tayyari.interview.tasks import TEMPLATES
from tayyari.llm.errors import ClaudeOverloaded
from tayyari.prompts.interview import ASSISTANT_SYSTEM
from tests.unit.interview.flows import say, start
from tests.unit.interview.support import CTX, RUN, SAMPLE, FakeRoles, headout_job, judged, make_rig

WORK = """export function CartSummary({ items, coupon }: Props) {
  const [discount, setDiscount] = useState(0);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let ignore = false;
    if (coupon) {
      setPending(true);
      applyCoupon(coupon).then((off) => {
        if (!ignore) { setDiscount(off); setPending(false); }
      });
    }
    return () => { ignore = true; };
  }, [coupon]);

  const total = sum(items) - discount;
  return <p className="total">{pending ? "Applying coupon…" : formatINR(total)}</p>;
}"""
NOTE = "Apply a coupon, then change it quickly twice; the total should end on the last one."
ANSWER_1 = "The total can be worked out from the items and the discount, so I do not keep a second copy that can disagree."
ANSWER_2 = (
    "While the call is pending the page shows Applying coupon, and an older answer is ignored by the flag."
)
ANSWER_3 = "I kept its flag but renamed it, and I added a pending state because it did not show anything while waiting."
SNIPPET = "```tsx\nlet ignore = false;\nreturn () => { ignore = true; };\n```"
MORE = "Because the effect sets the full total first, then the discounted one when the coupon call resolves."


def submit(work: str = WORK, note: str | None = NOTE) -> WorkSubmit:
    return WorkSubmit(profileId=SAMPLE.id, work=work, testNote=note)


def ask(message: str, work: str | None = None) -> AssistantRequest:
    return AssistantRequest(profileId=SAMPLE.id, message=message, work=work)


# ───────────────────────── starting a work sample ─────────────────────────


async def test_a_work_sample_starts_with_the_task_and_no_phrasing_call() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    assert rig.claude.calls == []
    assert (session.kind, session.title, session.status, session.minutes) == (
        "work",
        "Work sample · Frontend engineer",
        "active",
        19,
    )
    assert session.task is not None and session.task == TEMPLATES[0].to_task()
    assert session.task.materialName == "CartSummary.tsx" and session.task.assistant is True
    assert [(s.id, s.kind, s.topic) for s in session.slots] == [
        ("s1", "task", "Deriving the total"),
        ("s2", "task", "A slow coupon call"),
        ("s3", "task", "Using the assistant"),
    ]
    line = session.current
    assert line is not None and (line.kind, line.slotId, line.rung) == ("question", None, None)
    assert line.text == TEMPLATES[0].task
    assert (session.work, session.testNote, session.assistantTurns) == (None, None, [])
    assert [(t.speaker, t.text, t.at) for t in session.turns] == [("interviewer", TEMPLATES[0].task, 0)]
    dumped = session.model_dump_json()
    for hidden in (
        *TEMPLATES[0].checks,
        "Why work out the total during render",
        "The assistant suggested useMemo",
    ):
        assert hidden not in dumped  # the checks and the questions stay on the server


async def test_the_family_comes_from_the_role_it_is_asked_for() -> None:
    nurse = headout_job(title="Staff Nurse", employer="Apollo")
    rig = make_rig(roles=FakeRoles({(SAMPLE.id, RUN.runId, RUN.jobId): (nurse, "A ward posting")}))
    session = await start(rig, "work", role=RUN)
    assert session.title == "Work sample · Nursing" and session.role == RUN
    assert (
        session.task is not None and session.task.family == "nursing" and session.task.materialKind == "text"
    )


async def test_no_template_for_this_kind_of_work_is_refused_with_the_way_out() -> None:
    manager = SAMPLE.model_copy(
        update={"person": SAMPLE.person.model_copy(update={"currentRole": "Product Manager"})}
    )
    rig = make_rig()
    with pytest.raises(NoTemplate) as refused:
        await rig.service.start(manager, StartRequest(profileId=manager.id, kind="work"), CTX)
    assert "Practise This role instead" in refused.value.user_message and rig.repo.rows == {}


async def test_skipping_every_follow_up_leaves_nothing_to_ask() -> None:
    rig = make_rig()
    with pytest.raises(BadPlan):
        await rig.service.start(
            SAMPLE, StartRequest(profileId=SAMPLE.id, kind="work", skip=["s1", "s2", "s3"]), CTX
        )
    session = await rig.service.start(
        SAMPLE, StartRequest(profileId=SAMPLE.id, kind="work", skip=["s3"]), CTX
    )
    assert [s.id for s in session.slots] == ["s1", "s2"] and session.minutes == 16


# ───────────────────────── handing in the work ─────────────────────────


async def test_the_work_comes_first_then_the_interviewer_asks_about_it() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    with pytest.raises(WorkFirst):
        await say(rig, session.id, ANSWER_1)
    rig.clock.advance(360)
    got = await rig.service.submit_work(SAMPLE, session.id, submit())
    assert rig.claude.calls == []  # no model call
    assert (got.work, got.testNote) == (WORK, NOTE)
    line = got.current
    assert line is not None and (line.kind, line.slotId, line.rung) == ("question", "s1", "surface")
    assert line.text == "Why work out the total during render instead of storing it?"
    assert got.slotIndex == 0 and [(t.speaker, t.at) for t in got.turns] == [
        ("interviewer", 0),
        ("interviewer", 360),
    ]


async def test_handing_in_twice_changes_nothing() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    await rig.service.submit_work(SAMPLE, session.id, submit())
    version = rig.repo.rows[session.id].version
    again = await rig.service.submit_work(SAMPLE, session.id, submit("a different version", None))
    assert again.work == WORK and rig.repo.rows[session.id].version == version


async def test_nothing_to_hand_in_is_refused_but_a_note_alone_is_enough() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    for empty in (submit("", None), submit("   \n", "  ")):
        with pytest.raises(BadPlan):
            await rig.service.submit_work(SAMPLE, session.id, empty)
    got = await rig.service.submit_work(
        SAMPLE, session.id, submit("", "I would talk it through: the effect sets two values.")
    )
    assert got.work == "" and got.current is not None and got.current.slotId == "s1"


# ───────────────────────── the assistant ─────────────────────────


async def test_the_assistant_answers_and_counts_what_was_asked() -> None:
    rig = make_rig(settings=InterviewSettings(assistant_messages=3))
    session = await start(rig, "work")
    rig.claude.script_structured("interview.assistant", {"reply": MORE})
    reply = await rig.service.assistant(
        SAMPLE, session.id, ask("why does the total flicker", work="draft one"), CTX
    )
    assert (reply.reply, reply.asked, reply.left) == (MORE, 1, 2)
    (call,) = rig.claude.structured_calls
    assert (call.ctx.purpose, call.effort, call.model) == ("interview.assistant", None, "claude-haiku-5-5")
    assert call.system_text == ASSISTANT_SYSTEM
    assert (
        "<task>\nThe cart total flickers" in call.user_text and "applyCoupon(coupon)" in call.user_text
    )  # the material
    assert (
        "<work>\ndraft one\n</work>" in call.user_text
        and "<message>\nwhy does the total flicker\n</message>" in call.user_text
    )
    assert SAMPLE.person.name not in call.user_text

    rig.claude.script_structured("interview.assistant", {"reply": SNIPPET})
    second = await rig.service.assistant(
        SAMPLE, session.id, ask("write a version that ignores stale coupon answers"), CTX
    )
    assert (second.asked, second.left) == (2, 1)
    later = rig.claude.structured_calls[1].user_text
    assert "Person: why does the total flicker\nAssistant: " + MORE in later  # the chat so far
    assert "<work>\n(nothing yet)\n</work>" in later

    view = await rig.service.view(SAMPLE, session.id)
    assert view is not None
    turns = view.session.assistantTurns
    assert turns is not None and [(t.you, t.assistant) for t in turns] == [
        ("why does the total flicker", MORE),
        ("write a version that ignores stale coupon answers", SNIPPET),
    ]


async def test_the_assistant_uses_the_work_handed_in_when_none_is_sent() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    await rig.service.submit_work(SAMPLE, session.id, submit())
    rig.claude.script_structured("interview.assistant", {"reply": MORE})
    await rig.service.assistant(SAMPLE, session.id, ask("is my cleanup right"), CTX)
    assert f"<work>\n{WORK}\n</work>" in rig.claude.structured_calls[0].user_text


async def test_a_message_over_the_sessions_allowance_is_refused() -> None:
    rig = make_rig(settings=InterviewSettings(assistant_messages=2))
    session = await start(rig, "work")
    for _ in range(2):
        rig.claude.script_structured("interview.assistant", {"reply": MORE})
        await rig.service.assistant(SAMPLE, session.id, ask("another question"), CTX)
    with pytest.raises(AssistantUnavailable) as used:
        await rig.service.assistant(SAMPLE, session.id, ask("one more"), CTX)
    assert used.value.user_message == "You've used this session's assistant messages."
    assert len(rig.claude.structured_calls) == 2


async def test_no_assistant_when_the_setting_is_zero() -> None:
    rig = make_rig(settings=InterviewSettings(assistant_messages=0))
    session = await start(rig, "work")
    with pytest.raises(AssistantUnavailable) as off:
        await rig.service.assistant(SAMPLE, session.id, ask("hello"), CTX)
    assert off.value.user_message == "The assistant isn't part of this practice."


async def test_an_empty_message_is_refused_and_a_failed_reply_is_not_counted() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    with pytest.raises(BadRequest):
        await rig.service.assistant(SAMPLE, session.id, ask("   "), CTX)
    rig.claude.script_structured("interview.assistant", ClaudeOverloaded())
    before = rig.repo.rows[session.id]
    with pytest.raises(ModelUnavailable):
        await rig.service.assistant(SAMPLE, session.id, ask("why does it flicker"), CTX)
    assert rig.repo.rows[session.id] == before


async def test_an_empty_or_odd_reply_never_reaches_the_person_empty() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    rig.claude.script_structured("interview.assistant", {"reply": "   "})
    reply = await rig.service.assistant(SAMPLE, session.id, ask("hello there"), CTX)
    assert reply.reply == "I couldn't answer that. Try asking it another way."
    rig.claude.script_structured("interview.assistant", {"reply": "word\n" * 600})
    long = await rig.service.assistant(SAMPLE, session.id, ask("hello again"), CTX)
    assert len(long.reply) <= 2001 and long.reply.endswith("…")


# ───────────────────────── the questions about the work ─────────────────────────


async def finish_follow_ups(rig: Any, session_id: str, *, third: bool) -> Any:
    first = await say(
        rig,
        session_id,
        ANSWER_1,
        verdict=judged(ANSWER_1, "defended", quote="worked out from the items and the discount"),
        after=60,
    )
    assert first.current is not None and first.current.slotId == "s2"
    assert first.current.text == "The coupon call now takes 3 seconds. What does the person see?"
    second = await say(
        rig,
        session_id,
        ANSWER_2,
        verdict=judged(ANSWER_2, "defended", quote="an older answer is ignored by the flag"),
        after=60,
    )
    if not third:
        return second
    assert second.current is not None and second.current.slotId == "s3"
    assert second.current.text == "The assistant suggested useMemo. Why did you change its version?"
    return await say(
        rig,
        session_id,
        ANSWER_3,
        verdict=judged(ANSWER_3, "shaky", quote="I kept its flag but renamed it"),
        after=60,
    )


async def test_the_assistant_question_is_skipped_when_the_assistant_was_never_used() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    await rig.service.submit_work(SAMPLE, session.id, submit())
    last = await finish_follow_ups(rig, session.id, third=False)
    assert last.current is not None and last.current.kind == "done" and last.slotIndex == 3


async def test_the_assistant_question_is_asked_when_it_was_used_even_late() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    await rig.service.submit_work(SAMPLE, session.id, submit())
    rig.claude.script_structured("interview.assistant", {"reply": SNIPPET})
    await rig.service.assistant(
        SAMPLE, session.id, ask("write a version that ignores stale coupon answers"), CTX
    )
    last = await finish_follow_ups(rig, session.id, third=True)
    assert last.current is not None and last.current.kind == "done"


async def test_the_judge_sees_the_task_the_work_and_the_note_as_context() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    await rig.service.submit_work(SAMPLE, session.id, submit())
    await finish_follow_ups(rig, session.id, third=False)
    call = rig.claude.calls_for("interview.judge")[0]
    assert "<context>\nTask: The cart total flickers when a coupon is applied." in call.user_text
    assert (
        "The person's work (CartSummary.tsx):" in call.user_text and "let ignore = false;" in call.user_text
    )
    assert f"How they would test it: {NOTE}" in call.user_text
    assert "<covers>" in call.user_text and "Names the extra render" in call.user_text


# ───────────────────────── the debrief ─────────────────────────


def checks_script() -> dict[str, Any]:
    c = TEMPLATES[0].checks
    return {
        "checks": [
            {
                "check": c[0],
                "quotes": ["Apply a coupon, then change it quickly twice"],
                "verdict": "defended",
            },
            {"check": c[1], "quotes": ["const total = sum(items) - discount;"], "verdict": "defended"},
            {"check": c[2], "quotes": ["Applying coupon…"], "verdict": "defended"},
            {"check": c[3], "quotes": ["I kept its flag but renamed it"], "verdict": "shaky"},
        ]
    }


async def test_the_debrief_of_a_work_sample_has_its_checks_and_what_was_seen_of_the_assistant() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    rig.claude.script_structured("interview.assistant", {"reply": "Use a cleanup flag:\n" + SNIPPET})
    await rig.service.assistant(
        SAMPLE, session.id, ask("write a version that ignores stale coupon answers"), CTX
    )
    await rig.service.submit_work(SAMPLE, session.id, submit())
    await finish_follow_ups(rig, session.id, third=True)
    rig.claude.script_structured("interview.debrief", {"slots": []})
    rig.claude.script_structured("interview.checks", checks_script())
    debrief = await rig.service.end(SAMPLE, session.id, CTX)

    purposes = sorted(
        c.ctx.purpose
        for c in rig.claude.structured_calls
        if c.ctx.purpose in ("interview.debrief", "interview.checks")
    )
    assert purposes == ["interview.checks", "interview.debrief"]
    assert debrief.kind == "work" and debrief.title == "Work sample · Frontend engineer"
    assert debrief.task == TEMPLATES[0].to_task() and debrief.work == WORK
    assert [(r.slotId, r.kind, r.verdict) for r in debrief.results] == [
        ("s1", "task", "defended"),
        ("s2", "task", "defended"),
        ("s3", "task", "shaky"),
    ]
    assert debrief.checks is not None
    assert [(c.check, c.verdict) for c in debrief.checks] == [
        (TEMPLATES[0].checks[0], "defended"),
        (TEMPLATES[0].checks[1], "defended"),
        (TEMPLATES[0].checks[2], "defended"),
        (TEMPLATES[0].checks[3], "shaky"),
    ]
    assert [c.quote for c in debrief.checks] == [
        "Apply a coupon, then change it quickly twice",
        "const total = sum(items) - discount",
        "Applying coupon…",
        "I kept its flag but renamed it",
    ]
    assert debrief.assistantUse == "Asked the assistant 1 thing · used its version as is"
    assert (
        debrief.firstThing is not None
        and debrief.firstThing.startswith("Using the assistant. ")
        and debrief.firstSlotId == "s3"
    )
    checks_call = rig.claude.calls_for("interview.checks")[0]
    assert (
        WORK in checks_call.user_text and NOTE in checks_call.user_text and ANSWER_1 in checks_call.user_text
    )
    assert debrief.results[2].say is not None and debrief.results[2].say.text.startswith(
        "“I [what you did first] because [why]"
    )


async def test_the_assistant_question_is_not_in_the_debrief_when_the_assistant_was_not_used() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    await rig.service.submit_work(SAMPLE, session.id, submit())
    await finish_follow_ups(rig, session.id, third=False)
    rig.claude.script_structured("interview.debrief", {"slots": []})
    rig.claude.script_structured("interview.checks", checks_script())
    debrief = await rig.service.end(SAMPLE, session.id, CTX)
    assert [r.slotId for r in debrief.results] == ["s1", "s2"] and debrief.counts.notAssessed == 0
    assert debrief.assistantUse is None and debrief.firstThing is None


async def test_a_check_with_a_quote_not_in_the_work_is_couldnt_defend() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    await rig.service.submit_work(SAMPLE, session.id, submit())
    await finish_follow_ups(rig, session.id, third=False)
    forged = checks_script()
    forged["checks"][1]["quotes"] = ["const total = useMemo(() => sum(items) - discount, [items, discount]);"]
    rig.claude.script_structured("interview.debrief", {"slots": []})
    rig.claude.script_structured("interview.checks", forged)
    debrief = await rig.service.end(SAMPLE, session.id, CTX)
    assert debrief.checks is not None
    assert (debrief.checks[1].verdict, debrief.checks[1].quote) == ("couldnt", None)
    assert debrief.checks[0].verdict == "defended"


async def test_ending_before_the_work_is_in_looks_at_nothing() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    debrief = await rig.service.end(SAMPLE, session.id, CTX)
    assert rig.claude.calls == []
    assert debrief.checks is not None and {c.verdict for c in debrief.checks} == {"not_assessed"}
    assert [r.verdict for r in debrief.results] == [
        "not_assessed",
        "not_assessed",
    ]  # the assistant question never applied
    assert debrief.work is None and debrief.task is not None and debrief.counts.notAssessed == 2


async def test_a_checks_call_that_fails_leaves_the_checks_not_assessed_not_a_failed_debrief() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    await rig.service.submit_work(SAMPLE, session.id, submit())
    await finish_follow_ups(rig, session.id, third=False)
    rig.claude.script_structured("interview.debrief", {"slots": []})
    rig.claude.script_structured("interview.checks", ClaudeOverloaded())
    debrief = await rig.service.end(SAMPLE, session.id, CTX)
    assert debrief.checks is not None and {c.verdict for c in debrief.checks} == {"not_assessed"}
    assert [r.verdict for r in debrief.results] == ["defended", "defended"]


async def test_asking_a_work_question_again_keeps_the_work_and_does_not_ask_for_it_again() -> None:
    rig = make_rig()
    session = await start(rig, "work")
    await rig.service.submit_work(SAMPLE, session.id, submit())
    await finish_follow_ups(rig, session.id, third=False)
    rig.claude.script_structured("interview.debrief", {"slots": []})
    rig.claude.script_structured("interview.checks", checks_script())
    await rig.service.end(SAMPLE, session.id, CTX)
    new = await rig.service.again(SAMPLE, session.id, "s2")
    assert new.kind == "work" and new.work == WORK and new.testNote == NOTE and new.minutes == 3
    assert new.current is not None and (new.current.slotId, new.current.text) == (
        "s1",
        "The coupon call now takes 3 seconds. What does the person see?",
    )
    assert new.task is not None and [s.id for s in new.slots] == ["s1"]
    got = await say(
        rig,
        new.id,
        ANSWER_2,
        verdict=judged(ANSWER_2, "defended", quote="an older answer is ignored by the flag"),
    )
    assert got.current is not None and got.current.kind == "done"
    unfinished = await start(rig, "work")
    rig.claude.script_structured("interview.debrief", {"slots": []})
    await rig.service.end(SAMPLE, unfinished.id, CTX)
    redo = await rig.service.again(SAMPLE, unfinished.id, "s1")
    assert (
        redo.work is None and redo.current is not None and redo.current.slotId is None
    )  # the task first, as there was no work
    with pytest.raises(WorkFirst):
        await say(rig, redo.id, ANSWER_1)
