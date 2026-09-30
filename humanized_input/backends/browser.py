"""Web pages in Chromium, via Playwright and the DevTools accessibility tree."""

from __future__ import annotations

from ..ax import AXItem, AXReader
from ..pointer import Rect
from . import Backend


class BrowserBackend(Backend):
    def __init__(self, page):
        self.page = page
        self.reader = AXReader(page)
        self._pointer = (0.0, 0.0)  # Playwright's mouse starts at the top-left

    def snapshot(self):
        return self.reader.snapshot()

    def focused(self) -> AXItem | None:
        item = self.reader.focused()
        if item is None or item.role in ("RootWebArea", ""):
            return None
        return item

    def focus(self, item: AXItem) -> None:
        if item.backend_id is not None:
            self.reader.focus(item.backend_id)

    def press(self, key: str, hold: float = 0.0) -> None:
        # Hold time is not simulated in the browser; Playwright sends down+up.
        self.page.keyboard.press(key)

    def settle(self) -> None:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass

    # --- mouse -----------------------------------------------------------

    def bounds(self, item: AXItem) -> Rect | None:
        if item.backend_id is None:
            return None
        box = self.reader.box(item.backend_id)
        return Rect(*box) if box and box[2] > 0 and box[3] > 0 else None

    def mouse_position(self) -> tuple[float, float]:
        return self._pointer

    def mouse_move(self, x: int, y: int) -> None:
        # steps=1: each call is one mouse report; pointer.py plans the path.
        self.page.mouse.move(x, y, steps=1)
        self._pointer = (x, y)

    def mouse_button(self, button: str, down: bool) -> None:
        if down:
            self.page.mouse.down(button=button)
        else:
            self.page.mouse.up(button=button)
