"""Web pages in Chromium, via Playwright and the DevTools accessibility tree."""

from __future__ import annotations

from ..ax import AXItem, AXReader
from . import Backend


class BrowserBackend(Backend):
    def __init__(self, page):
        self.page = page
        self.reader = AXReader(page)

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
