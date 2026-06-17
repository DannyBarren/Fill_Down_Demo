"""IP protection: License acceptance + simple demo login gate.

A lightweight, self-contained access layer shown *before* the main app:

    License screen  ->  Login screen  ->  Main app

State is kept in ``st.session_state`` so it persists across reruns within a
browser session. This is a **soft demo gate** (credentials live in the source and
are shown on screen on purpose) — its job is to put the proprietary license in
front of every visitor and keep casual access behind a shared credential, not to
provide cryptographic security.

Set ``BBD_DEMO_AUTH=0`` to disable the gate entirely (used by the test suite).
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

# --------------------------------------------------------------------------- #
# Demo credentials (intentionally visible — share only with approved prospects)
# --------------------------------------------------------------------------- #
DEMO_USERNAME = "BarrenBizDev"
DEMO_PASSWORD = "Bean_Cat_135_!$"

FOOTER_TEXT = ("© 2026 Barren Business Development — Demo Only | "
               "Commercial Use Requires Written Consent & Payment")

_FALLBACK_LICENSE = """# Barren Business Development - Fill Down Automation Demo License

Copyright © 2026 Danny Barren / Barren Business Development. All Rights Reserved.

**THIS IS A DEMO VERSION.**

This software is the exclusive intellectual property of Barren Business
Development (Danny Barren). All use requires written permission. Contact
barren.danny@gmail.com or (614) 440-3220 for a commercial license.
"""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def auth_enabled() -> bool:
    """True unless explicitly disabled via ``BBD_DEMO_AUTH=0`` (for tests)."""
    return os.environ.get("BBD_DEMO_AUTH", "1") != "0"


def _license_text() -> str:
    """Prefer the on-disk LICENSE.md; fall back to an embedded summary."""
    try:
        p = Path(__file__).resolve().parent.parent / "LICENSE.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return _FALLBACK_LICENSE


def license_accepted() -> bool:
    return bool(st.session_state.get("license_accepted"))


def logged_in() -> bool:
    return bool(st.session_state.get("logged_in"))


def show_license_requested() -> bool:
    return bool(st.session_state.get("show_license"))


# ---- callbacks (safe to use as on_click) ---------------------------------- #
def accept_license() -> None:
    st.session_state["license_accepted"] = True
    st.session_state["show_license"] = False


def request_license() -> None:
    st.session_state["show_license"] = True


def close_license() -> None:
    st.session_state["show_license"] = False


def logout() -> None:
    st.session_state["logged_in"] = False
    st.session_state["show_license"] = False


# --------------------------------------------------------------------------- #
# Screens
# --------------------------------------------------------------------------- #
def render_license(reshow: bool = False) -> None:
    """Full-page license view. First-time = must accept; re-show = just close."""
    st.markdown(_license_text())
    st.divider()
    if reshow or license_accepted():
        st.button("← Back to app", type="primary", key="license_close",
                  on_click=close_license)
    else:
        st.caption("You must accept the license to continue.")
        st.button("I Accept & Continue to Login", type="primary",
                  key="license_accept", on_click=accept_license)


def render_login() -> None:
    """Simple shared-credential login screen."""
    st.title("Barren Business Development Demo Access")
    st.write("This is a private demo. Share these credentials only with approved "
             "prospects.")
    st.info(f"**Username:** `{DEMO_USERNAME}`  \n**Password:** `{DEMO_PASSWORD}`")

    with st.form("bbd_login", clear_on_submit=False):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", type="primary")

    if submitted:
        if username == DEMO_USERNAME and password == DEMO_PASSWORD:
            st.session_state["logged_in"] = True
            st.rerun()
        else:
            st.error("Invalid credentials. Use the username and password shown "
                     "above.")

    st.divider()
    st.caption(FOOTER_TEXT)


def sidebar_account() -> None:
    """Account/license controls — call inside a ``with st.sidebar:`` block."""
    st.divider()
    st.caption("Signed in as **Demo User**")
    st.button("📜 License", width="stretch", key="sidebar_license",
              on_click=request_license)
    st.button("Log out", width="stretch", key="sidebar_logout",
              on_click=logout)


def render_footer() -> None:
    """Small proprietary footer shown on the main app pages."""
    st.divider()
    st.caption(FOOTER_TEXT)
