"""Backends connect the virtual screen reader and simulated user to something real.

A backend answers two questions a screen reader asks, and does one thing a
user does:

- What is on screen? (`snapshot`: title + reading order of AXItems)
- What has keyboard focus? (`focused`)
- Press a key. (`press`)

Backends that support a mouse also implement `bounds`, `mouse_position`,
`mouse_move` and `mouse_button`; the pointer model in pointer.py decides
where and when to call them.

Available backends:

- BrowserBackend (browser.py): a web page in Chromium via Playwright.
- WindowsUIABackend (windows_uia.py): any Windows desktop app via
  UI Automation, with real OS keystrokes via SendInput.
"""

from __future__ import annotations

from ..ax import AXItem


class Backend:
    # Web pages have a browse mode with quick-nav keys (H, F, B...). Desktop
    # apps do not: users Tab, use arrows, or explore with object navigation.
    browse_mode = True
    # Desktop screen readers read a dialog's text when it opens.
    reads_new_window_text = False
    # Which audit rules apply: "web" or "desktop".
    kind = "web"

    def snapshot(self) -> tuple[str, list[AXItem]]:
        raise NotImplementedError

    def focused(self) -> AXItem | None:
        raise NotImplementedError

    def focus(self, item: AXItem) -> None:
        """Move keyboard focus to `item` (the screen reader 'entering' it)."""
        raise NotImplementedError

    def press(self, key: str, hold: float = 0.0) -> None:
        """Press a key: a single character, a name like "Tab"/"Enter"/"Backspace",
        or a combination like "Shift+Tab". `hold` is how long it stays down."""
        raise NotImplementedError

    def settle(self) -> None:
        """Wait briefly for the app to react to input."""

    # --- mouse (optional) --------------------------------------------------

    def bounds(self, item: AXItem):
        """The item's on-screen Rect (see pointer.py), or None if it has none."""
        raise NotImplementedError

    def mouse_position(self) -> tuple[float, float]:
        raise NotImplementedError

    def mouse_move(self, x: int, y: int) -> None:
        """Move the pointer to (x, y) in one step; pointer.py plans the path."""
        raise NotImplementedError

    def mouse_button(self, button: str, down: bool) -> None:
        """Press (down=True) or release a mouse button: "left", "right", "middle"."""
        raise NotImplementedError


def __getattr__(name):
    # Lazy imports so importing this package never needs Playwright or Windows.
    if name == "BrowserBackend":
        from .browser import BrowserBackend
        return BrowserBackend
    if name == "WindowsUIABackend":
        from .windows_uia import WindowsUIABackend
        return WindowsUIABackend
    raise AttributeError(name)
