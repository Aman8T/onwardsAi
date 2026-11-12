/*
 * Boot — runs inline in <head> before any stylesheet, immediately after hue.js
 * (inlined with its exports stripped by the calmBoot() plugin in vite.config.ts),
 * so `hueAt` and `applyHue` are already in scope. It writes data-scheme, the hue and chroma
 * custom properties (--h … --kb and the --c-* numbers) and data-atmo on <html> so the very first paint has the
 * right scheme, look and colour. The look is quiet unless tayyari.look is 'today' or ?look=today.
 *
 * No imports, no exports, no module syntax. Everything is guarded: if anything
 * fails, the stylesheet's `prefers-color-scheme` rule, the @property initial
 * values and the hex fallbacks in tokens.css apply.
 */
(function () {
  /** @param {string} key @returns {string | null} */
  function read(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  }

  /** @param {string} key @param {string} value */
  function write(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_) {
      /* storage blocked: the session still works, the value just does not persist */
    }
  }

  // The scheme first, on its own: a fixed choice (or ?scheme=light|dark for screenshots) goes on
  // <html data-scheme>; System leaves it off so the stylesheet follows the device.
  try {
    var params = new URLSearchParams(window.location.search);
    var pinned = params.get('scheme');
    var scheme = pinned === 'light' || pinned === 'dark' ? pinned : read('tayyari.scheme');
    if (scheme === 'light' || scheme === 'dark') document.documentElement.dataset.scheme = scheme;
    else delete document.documentElement.dataset.scheme;
  } catch (_) {
    /* the media query decides */
  }

  // The look: quiet is the default. localStorage tayyari.look = 'today' (or ?look=today) leaves the
  // attribute off; ?look=quiet pins quiet. A query value beats storage, as ?scheme= does.
  try {
    var lookQuery = new URLSearchParams(window.location.search).get('look');
    var look = lookQuery === 'today' || lookQuery === 'quiet' ? lookQuery : read('tayyari.look');
    if (look === 'today') delete document.documentElement.dataset.look;
    else document.documentElement.dataset.look = 'quiet';
  } catch (_) {
    /* the stylesheet's default look applies */
  }

  try {
    var root = document.documentElement;

    // Per-install hue offset, 0..359, created once and kept.
    var h0 = parseInt(read('tayyari.hue0') || '', 10);
    if (!(h0 >= 0 && h0 < 360)) {
      h0 = Math.floor(Math.random() * 360);
      write('tayyari.hue0', String(h0));
    }

    // Chosen mode (what the Atmosphere control shows).
    var mode = read('tayyari.atmo');
    if (mode !== 'drift' && mode !== 'still' && mode !== 'off') mode = 'drift';

    // ?h=<deg> pins the base hue for previews and screenshots; it forces Still.
    var query = new URLSearchParams(window.location.search).get('h');
    var forced = query === null ? NaN : parseInt(query, 10);
    var hasForced = isFinite(forced);

    var now = Date.now();
    var v = hueAt(now, h0);
    // Shifting the offset by the difference keeps the clock's lobe offsets and
    // breath, and recomputes the warm-sector easing for the forced hue.
    if (hasForced) v = hueAt(now, h0 + forced - v.h);

    var reduce =
      window.matchMedia('(prefers-reduced-motion: reduce)').matches ||
      window.matchMedia('(update: slow)').matches;
    var atmo = hasForced ? 'still' : mode === 'drift' && reduce ? 'still' : mode;

    applyHue(root, v);
    root.dataset.atmo = atmo;
  } catch (_) {
    /* fall through to the stylesheet's defaults */
  }
})();
