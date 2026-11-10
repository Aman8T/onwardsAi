"""Tracking addresses (folding, the fallback, the initial, the alphabet, a new draw each time) and the mail helpers (recipients
on our domain in every shape Resend may send, senders, HTML to text)."""

from __future__ import annotations

import itertools

import pytest

from tayyari.inbox.address import (
    ALPHABET,
    LOCAL_PART_RE,
    RANDOM_CHARS,
    fold,
    make_local_part,
    name_parts,
)
from tayyari.inbox.mail import (
    addresses_in,
    domain_of,
    html_to_text,
    local_parts_on,
    recipients_from,
    sender_of,
)

DOMAIN = "apply.leafout.in"


def fixed(chars: str = "k7m4q"):  # type: ignore[no-untyped-def]
    it = itertools.cycle(chars)
    return lambda alphabet: next(it)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Zoë", "zoe"),
        ("Ana-María", "anamaria"),
        ("O'Brien", "obrien"),
        ("JORDAN", "jordan"),
        ("Łukasz", "ukasz"),  # Ł has no decomposition: dropped, never a non-ASCII letter
        ("李", ""),
        ("Jean 2nd", "jeannd"),
        (None, ""),
    ],
)
def test_fold_keeps_plain_lower_case_letters_only(name: str | None, expected: str) -> None:
    assert fold(name) == expected


def test_a_local_part_is_first_name_dot_initial_and_five_random() -> None:
    assert make_local_part("Jordan", "Kapoor", choice=fixed()) == "jordan.kk7m4q"
    assert make_local_part("Jordan", None, choice=fixed()) == "jordan.k7m4q"


def test_no_first_name_falls_back_to_me_and_long_names_are_cut() -> None:
    assert make_local_part(None, None, choice=fixed()) == "me.k7m4q"
    assert make_local_part("李", "王", choice=fixed()) == "me.k7m4q"
    long = make_local_part("Venkatanarasimharajuvaripeta", "Rao", choice=fixed())
    assert long.split(".")[0] == "venkatanarasimharaju" and len(long.split(".")[0]) == 20


def test_random_draws_use_only_the_alphabet_and_differ() -> None:
    drawn = {make_local_part("Riya", "Shah") for _ in range(200)}
    assert len(drawn) > 190
    for local in drawn:
        suffix = local.split(".")[1][1:]
        assert len(suffix) == RANDOM_CHARS and set(suffix) <= set(ALPHABET)
        assert LOCAL_PART_RE.match(local)
        assert "+" not in local
    assert not set("ilo01") & set(ALPHABET)


def test_name_parts() -> None:
    assert name_parts("Meera  Nair") == ("Meera", "Nair")
    assert name_parts("Cher") == ("Cher", None)
    assert name_parts("Ana María de la Cruz") == ("Ana", "Cruz")
    assert name_parts(None) == (None, None)


# ───────────────────────── recipients ─────────────────────────


def test_recipients_in_every_shape_and_case() -> None:
    data = {
        "to": ["Jordan <Jordan.K7M4Q@Apply.Leafout.in>", "someone@gmail.com"],
        "received_for": "riya.s23456@apply.leafout.in",
        "recipients": [{"email": "me.abcde@APPLY.LEAFOUT.IN", "name": "Me"}],
    }
    assert recipients_from(data, DOMAIN) == ["jordan.k7m4q", "riya.s23456", "me.abcde"]


def test_a_comma_separated_string_and_duplicates() -> None:
    value = "a.bcdef@apply.leafout.in, <a.bcdef@apply.leafout.in>, x@other.in"
    assert local_parts_on(DOMAIN, value) == ["a.bcdef"]


def test_other_domains_and_lookalikes_are_not_ours() -> None:
    data = {"to": ["a.bcdef@apply.leafout.in.evil.com", "a.bcdef@leafout.in", "a.bcdef@xapply.leafout.in"]}
    assert recipients_from(data, DOMAIN) == []
    assert recipients_from({"to": None}, DOMAIN) == []
    assert recipients_from({"to": 42}, DOMAIN) == []


def test_senders_and_domains() -> None:
    assert sender_of("Greenhouse <No-Reply@Greenhouse.io>") == "no-reply@greenhouse.io"
    assert sender_of({"email": "jobs@acme.com", "name": "Acme"}) == "jobs@acme.com"
    assert sender_of(None) == "" and sender_of("not an address") == ""
    assert domain_of("jobs@Mail.Acme.com.") == "mail.acme.com"
    assert domain_of("nobody") == ""
    assert addresses_in(["a@b.co", "A@B.CO"]) == ["a@b.co"]


def test_html_to_text_drops_tags_scripts_and_styles() -> None:
    markup = (
        "<html><head><title>T</title><style>p{color:red}</style></head><body>"
        "<p>Hi Riya,</p><p>Thanks for applying to <b>Acme</b>&nbsp;&amp; co.</p>"
        "<script>alert(1)</script><ul><li>One</li><li>Two</li></ul><br/>Bye</body></html>"
    )
    text = html_to_text(markup)
    assert text.splitlines()[0] == "Hi Riya,"
    assert "Thanks for applying to Acme & co." in text
    assert "alert" not in text and "color" not in text and "<" not in text
    assert "One\n\nTwo" in text or "One\nTwo" in text
    assert text.endswith("Bye")
