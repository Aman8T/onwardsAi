"""Read a live form: its fields, their current values and its blockers, from every frame of the page.

`SCAN_JS` is ported from ``experiments/auto_apply/common/formscan.py`` (it read all six measured forms there). It runs in
each frame and returns plain JSON; `parse_scan` turns the frames' results into `forms.FormField` rows, so the planner, the
field-list loop and the DOM check all work on the state the browser actually holds, never on what a step said it did.

What the live scan sees that `forms.scan_form` (saved HTML) cannot: fields inside iframes, react-select comboboxes (and not
their hidden ``aria-hidden`` twins), visibility, current values, checkbox groups, and a resume widget that already took
its file (a ``synthetic`` field, never filled). Text read from the page is data, never instructions.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from tayyari.apply.forms import FormField, clean
from tayyari.contract.apply import FieldKind

SCAN_JS: Final[str] = r"""(resumeName) => {
  const txt = s => (s || '').replace(/\s+/g, ' ').trim();
  const GENERIC = /^(attach|upload|choose( a)? file|browse|select( a)? file|drop files?|or drag)/i;
  const groupLabel = el => {
    const g = el.closest('[role=group][aria-labelledby], [role=group][aria-label]');
    if (g) { const id = g.getAttribute('aria-labelledby'); const t = txt(id ? (document.getElementById(id) || {}).innerText : g.getAttribute('aria-label'));
             if (t) return t; }
    let p = el.parentElement;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      for (const c of p.querySelectorAll('[id$="-label"], label, legend, h2, h3, h4, [class*="label" i], [class*="title" i]')) {
        const t = txt(c.innerText); if (t && !GENERIC.test(t) && !c.contains(el) && t.length < 120) return t; } }
    return ''; };
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const labelOf = el => {
    let t = '';
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) t = l.innerText; }
    if (!t && el.getAttribute('aria-labelledby'))
      t = el.getAttribute('aria-labelledby').split(/\s+/).map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
    if (!t) { const l = el.closest('label'); if (l) t = l.innerText; }
    if (!t) t = el.getAttribute('aria-label') || '';
    if (!t) { let p = el.parentElement;
      for (let i = 0; i < 5 && p && !t; i++, p = p.parentElement) {
        const lab = p.querySelector('label, legend, [class*="label" i], [class*="question" i]');
        if (lab && !lab.contains(el) && txt(lab.innerText)) t = lab.innerText; } }
    if (!t) t = el.getAttribute('placeholder') || el.getAttribute('name') || '';
    return txt(t).slice(0, 220);
  };
  const sel = el => {
    if (el.id) return '[id="' + el.id.replace(/"/g, '\\"') + '"]';
    const n = el.getAttribute('name');
    if (n && document.querySelectorAll(el.tagName + '[name="' + CSS.escape(n) + '"]').length === 1)
      return el.tagName.toLowerCase() + '[name="' + n.replace(/"/g, '\\"') + '"]';
    const path = []; let e = el;
    while (e && e.nodeType === 1 && e !== document.body && path.length < 10) {
      let i = 1, s = e; while ((s = s.previousElementSibling)) if (s.tagName === e.tagName) i++;
      path.unshift(e.tagName.toLowerCase() + ':nth-of-type(' + i + ')'); e = e.parentElement; }
    return 'body > ' + path.join(' > ');
  };
  const out = []; const seenGroups = new Set();
  for (const el of document.querySelectorAll('input, select, textarea')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) continue;
    if (el.getAttribute('aria-hidden') === 'true') continue;  // react-select's hidden "requiredInput" twin
    const combo = el.getAttribute('role') === 'combobox';
    if (type !== 'file' && !vis(el) && !combo) continue;
    if (combo && !vis(el) && !vis(el.parentElement || el)) continue;
    let label = labelOf(el);
    if (type === 'file' && GENERIC.test(label)) label = groupLabel(el) || label;
    const f = { selector: sel(el), tag: el.tagName.toLowerCase(), type: combo ? 'combobox' : type,
                name: el.getAttribute('name'), label,
                required: !!(el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label)) };
    if (type === 'radio' || (type === 'checkbox' && el.name && document.querySelectorAll('input[type=checkbox][name="' + CSS.escape(el.name) + '"]').length > 1)) {
      const key = type + ':' + el.name; if (seenGroups.has(key)) continue; seenGroups.add(key);
      const group = [...document.querySelectorAll('input[type=' + type + '][name="' + CSS.escape(el.name) + '"]')];
      const fs = el.closest('fieldset'); const legend = fs && fs.querySelector('legend');
      let q = legend ? txt(legend.innerText) : '';
      if (!q) { let p = el.parentElement; for (let i = 0; i < 6 && p && !q; i++, p = p.parentElement) {
          const lab = p.querySelector('label:not(:has(input)), legend, [class*="label" i], [class*="question" i]');
          if (lab && !group.some(g => lab.contains(g)) && txt(lab.innerText)) q = txt(lab.innerText); } }
      f.type = type === 'radio' ? 'radio' : 'checkbox-group'; f.label = (q || label).slice(0, 220);
      f.options = group.map(r => txt(labelOf(r)).slice(0, 80));
      f.value = group.filter(r => r.checked).map(r => txt(labelOf(r))).join('; ');
      f.required = group.some(r => r.required || r.getAttribute('aria-required') === 'true') || /\*\s*$/.test(f.label);
    } else if (type === 'checkbox') { f.value = el.checked ? 'checked' : '';
    } else if (type === 'file') { f.value = el.files && el.files.length ? el.files[0].name : '';
      f.accept = el.getAttribute('accept') || '';
    } else if (el.tagName === 'SELECT') {
      f.options = [...el.options].map(o => txt(o.text)).slice(0, 80);
      const o = el.options[el.selectedIndex]; f.value = o && o.value ? txt(o.text) : '';
    } else if (combo) {
      let box = el.parentElement; let v = '';
      for (let i = 0; i < 5 && box && !v; i++, box = box.parentElement) {
        const sv = box.querySelector('[class*="single-value" i], [class*="singleValue" i], [class*="multi-value__label" i], [class*="multiValue" i] > div');
        if (sv && !sv.contains(el)) v = txt(sv.innerText); }
      f.value = v || txt(el.value);
    } else { f.value = txt(el.value).slice(0, 400); }
    out.push(f);
  }
  for (const g of document.querySelectorAll('[role=group]')) {
    const id = g.getAttribute('aria-labelledby'); const t = txt(id ? (document.getElementById(id) || {}).innerText : g.getAttribute('aria-label'));
    if (!/resume|\bcv\b|curriculum/i.test(t) || g.querySelector('input[type=file]') || !g.querySelector('[role=progressbar]')) continue;
    out.push({ selector: '', tag: 'widget', type: 'file', name: null, label: t + ' (attached; upload held by the guard)',
               required: g.getAttribute('aria-required') === 'true' || /\*\s*$/.test(t), value: '(file attached, upload held)' });
  }
  // The resume counts as shown only when its full file name (with extension, never a generic name such as resume.pdf)
  // is inside a file widget's own container: a file input's near ancestors or a resume/CV group, never the whole page.
  const resumeShown = () => {
    const want = txt(resumeName);
    if (!want || !/\.[a-z0-9]{2,5}$/i.test(want) || /^(my[ _-]?)?(resume|cv|curriculum[ _-]?vitae)\.[a-z0-9]{2,5}$/i.test(want)) return false;
    const boxes = [];
    for (const inp of document.querySelectorAll('input[type=file]')) {
      let p = inp.parentElement; for (let i = 0; i < 3 && p && p !== document.body; i++, p = p.parentElement) boxes.push(p); }
    for (const g of document.querySelectorAll('[role=group]')) {
      const id = g.getAttribute('aria-labelledby'); const t = txt(id ? (document.getElementById(id) || {}).innerText : g.getAttribute('aria-label'));
      if (/resume|\bcv\b|curriculum/i.test(t)) boxes.push(g); }
    return boxes.some(b => txt(b.innerText).includes(want));
  };
  const html = document.documentElement.innerHTML.toLowerCase();
  const blockers = {
    captcha_on_page: /g-recaptcha|h-captcha|cf-turnstile|recaptcha\/api|hcaptcha\.com|grecaptcha/.test(html),
    password_field: !!document.querySelector('input[type=password]'),
    submit_texts: [...document.querySelectorAll('button, input[type=submit], [role=button]')]
      .map(b => txt(b.innerText || b.value)).filter(t => /submit|send|apply/i.test(t)).slice(0, 5),
    resume_name_on_page: resumeShown(),
  };
  return { url: location.href, title: document.title, fields: out, blockers };
}"""
"""Evaluated once per frame with the resume's file name (or ''), which lets it notice a widget that shows the attached
file's name in place of its input. Only the full, non-generic file name inside a file widget's own container counts
(`resume_name_inferable`); the submit pass must still see the flag go from false before the attach to true after it."""

_GENERIC_RESUME: Final = re.compile(
    r"^(?:my[ _-]?)?(?:resume|cv|curriculum[ _-]?vitae)\.[a-z0-9]{2,5}$", re.I
)
_HAS_EXTENSION: Final = re.compile(r"\.[a-z0-9]{2,5}$", re.I)


def resume_name_inferable(name: str) -> bool:
    """True when a resume's file name is specific enough to recognise on a page: it has an extension and is not a
    generic name such as ``resume.pdf`` or ``CV.docx``, which any form's own text could contain."""
    name = name.strip()
    return bool(name) and bool(_HAS_EXTENSION.search(name)) and not _GENERIC_RESUME.match(name)


_KINDS: Final[dict[str, FieldKind]] = {
    "text": "text", "email": "email", "tel": "tel", "url": "url", "number": "number", "date": "date",
    "textarea": "textarea", "select": "select", "radio": "radio", "checkbox": "checkbox", "file": "file",
    "combobox": "combobox", "checkbox-group": "checkboxGroup",
}  # fmt: skip
_SKIP_TYPES: Final = frozenset({"password", "search"})
"""A password box is a login wall (`ScanBlockers.password_field`), a search box is the site's own search: neither is a
field of the application."""
_REQUIRED_MARK: Final = re.compile(r"\s*(?:[*✱]\s*)?\(required\)\s*$|\s*[*✱]\s*$", re.I)
_ID_SELECTOR: Final = re.compile(r'^\[id="((?:[^"\\]|\\.)+)"\]$')
_NAME_SELECTOR: Final = re.compile(r'^(?:\w+)?\[name="((?:[^"\\]|\\.)+)"\]$')
_HONEYPOT: Final = re.compile(r"honey-?pot", re.I)
_PLACEHOLDER_OPTION: Final = re.compile(
    r"^(?:-+|select(?:\.\.\.|…)?|choose(?:\.\.\.|…)?|please select.*|select an? .*)$", re.I
)
_MAX_SUBMIT_TEXTS: Final = 10


@dataclass(frozen=True, slots=True)
class ScanBlockers:
    captcha_on_page: bool = False
    """A CAPTCHA script or widget is in the HTML. Too broad to stop a fill on its own (four measured forms carried one and
    still filled); `forms.blocker_of` decides on a visible challenge."""
    password_field: bool = False
    submit_texts: tuple[str, ...] = ()
    """Texts of the page's submit-like buttons (``Submit application``), for the submit step to find its control."""
    resume_name_on_page: bool = False


@dataclass(slots=True)
class LiveScan:
    url: str
    fields: list[FormField]
    blockers: ScanBlockers = field(default_factory=ScanBlockers)
    title: str = ""

    @property
    def actionable(self) -> list[FormField]:
        """The fields a step can act on (synthetic ones left out)."""
        return [f for f in self.fields if not f.synthetic]

    @property
    def has_form(self) -> bool:
        """Two or more fields to fill: a form is on screen (a lone newsletter box is not one)."""
        return len(self.actionable) >= 2


def _text(value: Any, limit: int = 400) -> str:
    return clean(value)[:limit] if isinstance(value, str) else ""


def _unescape(selector_value: str) -> str:
    return re.sub(r"\\(.)", r"\1", selector_value)


def _quoted(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _field(raw: Mapping[str, Any], frame: int) -> FormField | None:
    kind_raw = _text(raw.get("type")).lower()
    if kind_raw in _SKIP_TYPES:
        return None
    selector = _text(raw.get("selector"), 2000)
    name = _text(raw.get("name"), 400) or None
    if _HONEYPOT.search(selector) or (name and _HONEYPOT.search(name)):
        return None  # a bot trap: a person never sees it, so it is never filled
    synthetic = raw.get("tag") == "widget" or not selector
    kind: FieldKind = _KINDS.get(kind_raw, "text")
    label = _text(raw.get("label"), 220)
    required = bool(raw.get("required"))
    if _REQUIRED_MARK.search(label):
        required = True
        label = _REQUIRED_MARK.sub("", label).strip()
    id_match = _ID_SELECTOR.match(selector)
    element_id = _unescape(id_match[1]) if id_match else None
    if kind in ("radio", "checkboxGroup") and name:
        selector, key = (
            f'[name="{_quoted(name)}"]',
            name,
        )  # a group is picked by its name, not by its first input's id
    elif element_id:
        key = element_id
    else:
        name_match = _NAME_SELECTOR.match(selector)
        key = _unescape(name_match[1]) if name_match else (name or selector or f"widget:{label}")
    options_raw = raw.get("options")
    texts = [_text(o, 200) for o in options_raw] if isinstance(options_raw, list) else []
    options = [(t, t) for t in texts if t and not (kind == "select" and _PLACEHOLDER_OPTION.match(t))]
    accept = raw.get("accept")
    return FormField(
        key=key, selector="" if synthetic else selector, kind=kind, label=label or key, required=required, name=name,
        id=element_id, options=options, frame=frame, value=_text(raw.get("value")), synthetic=synthetic,
        accept=accept if isinstance(accept, str) and accept else None,
    )  # fmt: skip


def parse_scan(frames: Sequence[Mapping[str, Any]], *, resume_name: str = "") -> LiveScan:
    """The live scan of a page from `SCAN_JS`'s result per frame (in frame order; an entry's own ``frame`` and each
    field's ``frame`` win, so a saved merged scan parses too). Keys are made unique in page order."""
    fields: list[FormField] = []
    url = title = ""
    captcha = password = resume_on_page = False
    submit_texts: list[str] = []
    for position, entry in enumerate(frames):
        frame_no = entry.get("frame", position)
        frame_no = frame_no if isinstance(frame_no, int) else position
        if position == 0:
            url, title = _text(entry.get("url"), 2000), _text(entry.get("title"), 300)
        raw_fields = entry.get("fields")
        for raw in raw_fields if isinstance(raw_fields, list) else []:
            if not isinstance(raw, Mapping):
                continue
            own = raw.get("frame", frame_no)
            item = _field(raw, own if isinstance(own, int) else frame_no)
            if item is not None:
                fields.append(item)
        blockers = entry.get("blockers")
        if isinstance(blockers, Mapping):
            captcha |= bool(blockers.get("captcha_on_page"))
            password |= bool(blockers.get("password_field"))
            resume_on_page |= bool(blockers.get("resume_name_on_page"))
            texts = blockers.get("submit_texts")
            submit_texts += (
                [_text(t, 80) for t in texts if isinstance(t, str)] if isinstance(texts, list) else []
            )
    resume_inputs = [f for f in fields if f.kind == "file" and "cover" not in f.label.lower()]
    if resume_on_page and resume_name_inferable(resume_name) and not any(f.value for f in resume_inputs):
        # Many ATS widgets replace the file input with a "file name + remove" chip once a file is attached.
        fields.append(FormField(key="widget:resume", selector="", kind="file", label="Resume/CV (attached)",
                                required=False, value=resume_name, synthetic=True))  # fmt: skip
    seen: dict[str, int] = {}
    for item in fields:
        count = seen.get(item.key, 0)
        seen[item.key] = count + 1
        if count:
            item.key = f"{item.key}~{count + 1}"
    unique_texts = tuple(dict.fromkeys(t for t in submit_texts if t))[:_MAX_SUBMIT_TEXTS]
    return LiveScan(url, fields, ScanBlockers(captcha, password, unique_texts, resume_on_page), title)


__all__ = ["SCAN_JS", "LiveScan", "ScanBlockers", "parse_scan", "resume_name_inferable"]
