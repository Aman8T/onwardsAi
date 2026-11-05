"""Read a form's fields and their current values from the live page (every frame), and spot blockers.

The same scan feeds Arm B's model and the scorer for every arm, so all arms are judged on the DOM state the browser
actually holds after the run, not on what an agent says it did.
"""

from __future__ import annotations

from typing import Any

SCAN_JS = r"""(resumeName) => {
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
  // A file widget that already took the file and is uploading it (the guard holds the upload): no input left, a
  // progress bar in its group. Counted as attached, flagged as "upload held".
  for (const g of document.querySelectorAll('[role=group]')) {
    const id = g.getAttribute('aria-labelledby'); const t = txt(id ? (document.getElementById(id) || {}).innerText : g.getAttribute('aria-label'));
    if (!/resume|\bcv\b|curriculum/i.test(t) || g.querySelector('input[type=file]') || !g.querySelector('[role=progressbar]')) continue;
    out.push({ selector: '', tag: 'widget', type: 'file', name: null, label: t + ' (attached; upload held by the guard)',
               required: g.getAttribute('aria-required') === 'true' || /\*\s*$/.test(t), value: '(file attached, upload held)' });
  }
  const html = document.documentElement.innerHTML.toLowerCase();
  const blockers = {
    captcha_on_page: /g-recaptcha|h-captcha|cf-turnstile|recaptcha\/api|hcaptcha\.com|grecaptcha/.test(html),
    password_field: !!document.querySelector('input[type=password]'),
    submit_texts: [...document.querySelectorAll('button, input[type=submit], [role=button]')]
      .map(b => txt(b.innerText || b.value)).filter(t => /submit|send|apply/i.test(t)).slice(0, 5),
    resume_name_on_page: !!(resumeName && document.body && document.body.innerText.includes(resumeName.replace(/\.pdf$/i, ''))),
  };
  return { url: location.href, title: document.title, fields: out, blockers };
}"""


def scan_page(page: Any) -> dict[str, Any]:
    """All frames of the page; each field carries the index of its frame (0 = main)."""
    from .profile import RESUME_NAME

    fields: list[dict[str, Any]] = []
    blockers = {"captcha_on_page": False, "password_field": False, "submit_texts": [], "resume_name_on_page": False}
    url, title = page.url, ""
    for index, frame in enumerate(page.frames):
        furl = frame.url or ""
        if not furl.startswith("http") and index != 0:
            continue
        try:
            got = frame.evaluate(SCAN_JS, RESUME_NAME)
        except Exception:
            continue
        if index == 0:
            url, title = got["url"], got["title"]
        for f in got["fields"]:
            f["frame"] = index
            fields.append(f)
        b = got["blockers"]
        for key in ("captcha_on_page", "password_field", "resume_name_on_page"):
            blockers[key] |= b[key]
        blockers["submit_texts"] += b["submit_texts"]
    # Many ATS widgets replace the <input type=file> with a "file name + remove" chip once a file is attached.
    resume_inputs = [f for f in fields if f["type"] == "file" and "cover" not in (f["label"] or "").lower()]
    if blockers["resume_name_on_page"] and not any(f.get("value") for f in resume_inputs):
        fields.append({"selector": "", "tag": "widget", "type": "file", "name": None, "frame": 0,
                       "label": "Resume/CV (attached; the widget replaced its file input)", "required": False,
                       "value": RESUME_NAME})  # fmt: skip
    return {"url": url, "title": title, "fields": fields, "blockers": blockers}


def best_page(context: Any) -> Any:
    """The open tab with the most form fields (an agent may have opened the form in a new tab)."""
    best, best_count = None, -1
    for page in context.pages:
        try:
            count = len(scan_page(page)["fields"])
        except Exception:
            continue
        if count > best_count:
            best, best_count = page, count
    return best
