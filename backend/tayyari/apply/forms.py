"""The fields of an application form, read from its HTML with the standard library's parser.

The engine reads the live page's HTML (``page.content()``) through this same code, so tests run it on saved pages without a
browser. A field's selector is ``[id="..."]`` when it has an id, else ``[name="..."]``. Labels come from ``<label for>``, a
wrapping ``<label>``, ``aria-label`` or the placeholder. Hidden, submit and button inputs are not fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from tayyari.contract.apply import FieldKind

_WS = re.compile(r"\s+")
_REQUIRED_MARK = re.compile(r"[*✱]\s*$")
_SKIP_TYPES = {"hidden", "submit", "button", "reset", "image", "search"}
_KINDS: dict[str, FieldKind] = {
    "text": "text", "email": "email", "tel": "tel", "url": "url", "number": "number", "date": "date",
    "checkbox": "checkbox", "radio": "radio", "file": "file",
}  # fmt: skip


@dataclass(slots=True)
class FormField:
    key: str
    selector: str
    kind: FieldKind
    label: str
    required: bool
    name: str | None = None
    id: str | None = None
    options: list[tuple[str, str]] = field(default_factory=list)
    """``(value, text)`` of a select's options (the placeholder option without a value is left out)."""


@dataclass(slots=True)
class FormScan:
    fields: list[FormField]
    submit_label: str | None
    has_form: bool


def clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _selector(attrs: dict[str, str]) -> tuple[str, str] | None:
    if attrs.get("id"):
        return attrs["id"], f'[id="{attrs["id"]}"]'
    if attrs.get("name"):
        return attrs["name"], f'[name="{attrs["name"]}"]'
    return None


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: list[FormField] = []
        self.label_for: dict[str, str] = {}
        self.has_form = False
        self.submit_label: str | None = None
        self._label: list[str] | None = None
        self._label_target: str | None = None
        self._wrapped: list[FormField] = []
        self._select: FormField | None = None
        self._option: tuple[str, list[str]] | None = None
        self._button: list[str] | None = None
        self._button_submit = False
        self._radio_seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k: (v or "") for k, v in attrs_list}
        if tag == "form":
            self.has_form = True
        elif tag == "label":
            self._label, self._label_target, self._wrapped = [], attrs.get("for"), []
        elif tag in ("input", "textarea", "select"):
            self._field(tag, attrs)
        elif tag == "option" and self._select is not None:
            self._option = (attrs.get("value", ""), [])
        elif tag == "button":
            self._button, self._button_submit = [], attrs.get("type", "submit") == "submit"

    def _field(self, tag: str, attrs: dict[str, str]) -> None:
        kind_attr = attrs.get("type", "text").lower() if tag == "input" else tag
        if tag == "input" and kind_attr in ("submit",) and not self.submit_label:
            self.submit_label = clean(attrs.get("value", "")) or None
        if tag == "input" and kind_attr in _SKIP_TYPES:
            return
        found = _selector(attrs)
        if found is None:
            return
        key, selector = found
        if kind_attr == "radio":
            name = attrs.get("name", key)
            if name in self._radio_seen:
                return
            self._radio_seen.add(name)
            key, selector = name, f'[name="{name}"]'
        kind: FieldKind = (
            "textarea"
            if tag == "textarea"
            else "select"
            if tag == "select"
            else _KINDS.get(kind_attr, "text")
        )
        required = "required" in attrs or attrs.get("aria-required") == "true"
        label = clean(attrs.get("aria-label", "") or attrs.get("placeholder", ""))
        item = FormField(
            key, selector, kind, label, required, attrs.get("name") or None, attrs.get("id") or None
        )
        self.fields.append(item)
        if self._label is not None:
            self._wrapped.append(item)
        if tag == "select":
            self._select = item

    def handle_endtag(self, tag: str) -> None:
        if tag == "label" and self._label is not None:
            text = clean("".join(self._label))
            if self._label_target:
                self.label_for[self._label_target] = text
            for item in self._wrapped:
                item.label = text or item.label
            self._label = None
        elif tag == "option" and self._option is not None and self._select is not None:
            value, parts = self._option
            text = clean("".join(parts))
            if value or (text and not text.lower().startswith("select")):
                self._select.options.append((value or text, text))
            self._option = None
        elif tag == "select":
            self._select = None
        elif tag == "button" and self._button is not None:
            if self._button_submit and not self.submit_label:
                self.submit_label = clean("".join(self._button)) or None
            self._button = None

    def handle_data(self, data: str) -> None:
        if self._option is not None:
            self._option[1].append(data)
            return
        if self._label is not None and self._select is None:
            self._label.append(data)
        if self._button is not None:
            self._button.append(data)


def scan_form(page: str) -> FormScan:
    parser = _Parser()
    parser.feed(page)
    parser.close()
    for item in parser.fields:
        if item.id and item.id in parser.label_for:
            item.label = parser.label_for[item.id] or item.label
        if _REQUIRED_MARK.search(item.label):
            item.required = True
        item.label = _REQUIRED_MARK.sub("", item.label).strip() or item.key
    return FormScan(parser.fields, parser.submit_label, parser.has_form)


_CAPTCHA_BLOCKING = (
    re.compile(
        r"<iframe[^>]+src=[\"'][^\"']*(?:hcaptcha\.com|challenges\.cloudflare\.com|recaptcha/api2/(?:anchor|bframe))",
        re.I,
    ),
    re.compile(r"class=[\"'][^\"']*\b(?:cf-turnstile|h-captcha)\b", re.I),
    re.compile(r"class=[\"'][^\"']*\bg-recaptcha\b(?![^>]*data-size=[\"']invisible)", re.I),
)
_CAPTCHA_ON_SUBMIT = re.compile(
    r"g-recaptcha[^>]*data-size=[\"']invisible|grecaptcha\.execute|recaptcha/enterprise", re.I
)
_LOGIN = (
    re.compile(r"<input[^>]+type=[\"']password[\"']", re.I),
    re.compile(r"\b(?:sign in|log in) to (?:apply|continue)\b", re.I),
)


def blocker_of(page: str) -> str | None:
    """``captcha`` or ``login_wall`` when the page shows one before anything was filled; None otherwise."""
    if any(p.search(page) for p in _CAPTCHA_BLOCKING):
        return "captcha"
    if any(p.search(page) for p in _LOGIN):
        return "login_wall"
    return None


def checks_on_submit(page: str) -> bool:
    """True when the form runs an invisible bot check on submit (it may ask for a human then)."""
    return bool(_CAPTCHA_ON_SUBMIT.search(page))


__all__ = ["FormField", "FormScan", "blocker_of", "checks_on_submit", "clean", "scan_form"]
