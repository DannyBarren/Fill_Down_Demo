"""End-to-end test for the IP-protection gate: License -> Login -> App."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("FILLDOWN_DB_PATH",
                      str(Path(tempfile.mkdtemp()) / "access.db"))

from streamlit.testing.v1 import AppTest  # noqa: E402

from ui.access import DEMO_PASSWORD, DEMO_USERNAME  # noqa: E402

MAIN = str(Path(__file__).resolve().parent.parent / "main.py")


def _find(buttons, needle):
    return [b for b in buttons if needle.lower() in (b.label or "").lower()]


def test_license_login_app_flow():
    prev = os.environ.get("BBD_DEMO_AUTH")
    os.environ["BBD_DEMO_AUTH"] = "1"          # force the gate on for this test
    try:
        at = AppTest.from_file(MAIN, default_timeout=90).run()
        assert not at.exception, at.exception

        # 1) License screen first — accept button present, no login inputs yet.
        accept = _find(at.button, "Accept")
        assert accept, "license accept button missing on first load"
        assert len(at.text_input) == 0, "login should not show before accepting"
        accept[0].click().run()
        assert not at.exception, at.exception
        assert at.session_state["license_accepted"] is True

        # 2) Login screen — wrong credentials are rejected.
        assert len(at.text_input) >= 2, "login form should have 2 inputs"
        at.text_input[0].set_value("nope")
        at.text_input[1].set_value("wrong")
        _find(at.button, "Login")[0].click().run()
        assert at.session_state["logged_in"] is False
        assert at.error, "wrong credentials should show an error"

        # 3) Correct credentials unlock the app.
        at.text_input[0].set_value(DEMO_USERNAME)
        at.text_input[1].set_value(DEMO_PASSWORD)
        _find(at.button, "Login")[0].click().run()
        assert not at.exception, at.exception
        assert at.session_state["logged_in"] is True

        # 4) Main app is now reachable (dashboard title renders).
        titles = [t.value for t in at.title]
        assert any("Barren Business Development" in t for t in titles)
    finally:
        os.environ["BBD_DEMO_AUTH"] = "0" if prev is None else prev
