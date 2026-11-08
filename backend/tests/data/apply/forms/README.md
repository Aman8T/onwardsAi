Hand-made form fixtures for the A1 runner, the field-list loop and the DOM check (package F). Each `.json` file has the
shape `apply.scan.SCAN_JS` returns for one frame (`url`, `title`, `fields`, `blockers`), with every value empty, the
way a form looks before anything is filled. Field labels and options follow the measured Greenhouse board
(`../scans/greenhouse-cloudflare.json`) and a generic careers page; no real person's data.

- `greenhouse-react.json`: the new Greenhouse job board (react-select comboboxes for the phone country, the location
  and self-ID; a consent tick; the resume input).
- `careers-custom.json`: a careers page the rules cannot fully place: a required "Where do you live right now?", a
  required field whose label is an injected instruction, and an optional motivation question.
