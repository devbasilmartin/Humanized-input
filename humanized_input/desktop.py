"""Start or attach to a Windows desktop app and hand you a SimulatedUser.

    from humanized_input.desktop import desktop_session

    with desktop_session("expert", launch=["notepad.exe"], title=r".*Notepad") as user:
        user.sr.read_window()
        ...

Keystrokes are real (SendInput), so while a session runs, leave the keyboard
alone and do not switch windows. Stop a runaway script with Ctrl+C in the
console (the backend refuses to type into any window but the target app's,
and brings the target back to the front if focus is lost).
"""

from __future__ import annotations

import subprocess
import sys
from contextlib import contextmanager

from .profiles import Profile, load_profile
from .timing import RealClock
from .user import SimulatedUser


def find_window(title: str, timeout: float = 10.0):
    """Find a top-level window whose title matches the regular expression."""
    import uiautomation as auto

    win = auto.WindowControl(searchDepth=1, RegexName=title)
    if not win.Exists(maxSearchSeconds=timeout):
        raise TimeoutError(f"No window with title matching {title!r} after {timeout}s")
    return win


@contextmanager
def desktop_session(profile: Profile | str, title: str, launch: list[str] | str | None = None,
                    speech=None, clock=None, timeout: float = 10.0, close: bool = True,
                    popen_kwargs: dict | None = None):
    """Launch (optional) and attach to the window whose title matches `title`.

    The clock defaults to a RealClock: real apps need real time to react, and
    the pauses are what make the input human-paced.
    """
    if sys.platform != "win32":
        raise OSError("desktop_session needs Windows (UI Automation + SendInput)")
    from .backends.windows_uia import WindowsUIABackend

    if isinstance(profile, str):
        profile = load_profile(profile)
    proc = subprocess.Popen(launch, **(popen_kwargs or {})) if launch else None
    try:
        window = find_window(title, timeout)
        backend = WindowsUIABackend(window)
        backend.bring_to_front()
        yield SimulatedUser(backend, profile, speech=speech, clock=clock or RealClock())
    finally:
        if proc is not None and close and proc.poll() is None:
            proc.terminate()
