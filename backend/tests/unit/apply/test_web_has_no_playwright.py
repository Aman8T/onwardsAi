"""The web app starts without Playwright: the browser layer imports it lazily, inside the functions that open a browser.

Playwright is in the dev and worker dependency groups only (until the in-app worker needs it in production), so a top-level
import anywhere the app loads would break the deploy. A fresh interpreter imports the app, the container and the whole
browser layer, and must not have loaded any ``playwright`` module.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[3]

CODE = """
import sys
import tayyari.api.app
import tayyari.container
import tayyari.apply.browser
import tayyari.apply.browser_use
import tayyari.apply.driver
import tayyari.apply.netguard
import tayyari.apply.scan
loaded = sorted(m for m in sys.modules if m == "playwright" or m.startswith("playwright."))
sys.stdout.write(",".join(loaded) or "none")
"""


def test_importing_the_app_and_the_browser_layer_loads_no_playwright() -> None:
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env["TAYYARI_ENV"] = "test"
    result = subprocess.run(  # noqa: S603  (our own interpreter, a fixed script)
        [sys.executable, "-c", CODE],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == "none"
