"""Convenience wrapper that starts a browser and hands you a SimulatedUser."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

from .profiles import Profile, load_profile
from .user import SimulatedUser


def _chromium_path() -> str | None:
    env = os.environ.get("CHROMIUM_PATH")
    if env:
        return env
    preinstalled = Path("/opt/pw-browsers/chromium")
    return str(preinstalled) if preinstalled.exists() else None


@contextmanager
def user_session(url: str, profile: Profile | str, speech=None, clock=None, headless: bool = True):
    """Open `url` and yield a SimulatedUser driving it with `profile`."""
    if isinstance(profile, str):
        profile = load_profile(profile)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, executable_path=_chromium_path())
        try:
            page = browser.new_page()
            page.goto(url)
            yield SimulatedUser(page, profile, speech=speech, clock=clock)
        finally:
            browser.close()
