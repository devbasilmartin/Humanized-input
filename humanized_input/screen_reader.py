"""A small virtual screen reader.

It mimics the two modes real Windows screen readers (NVDA, JAWS) have:

- Browse mode: a "virtual cursor" moves through the page's reading order.
  Arrow keys go item by item; quick-nav keys jump by type (H heading,
  F form field, B button, K link, D landmark). These keys are consumed by the
  screen reader and never reach the web page.
- Focus mode: keys go to the page. Tab moves real keyboard focus, and the
  screen reader announces whatever gained focus.

On the Windows desktop there is no browse mode. There the virtual cursor
plays the role of NVDA's object navigation (NVDA+numpad keys), which lets a
user explore every element of a window and move focus to it.

Announcements are phrased roughly the way NVDA says them, and every one is
kept in a transcript so you can study (or assert on) what a user heard.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

from .ax import FORM_FIELD_ROLES, LANDMARK_ROLES, AXItem

ROLE_WORDS = {
    "textbox": "edit",
    "searchbox": "search edit",
    "checkbox": "check box",
    "radio": "radio button",
    "combobox": "combo box",
    "listbox": "list box",
    "image": "graphic",
    "img": "graphic",
    "spinbutton": "spin button",
    "contentinfo": "content info",
    "complementary": "complementary",
    "listitem": "list item",
    "treeitem": "tree view item",
    "tablist": "tab control",
    "menubar": "menu bar",
    "menuitem": "menu item",
    "toolbar": "tool bar",
    "group": "grouping",
    "progressbar": "progress bar",
    "status": "status bar",
}

QUICK_NAV: dict[str, Callable[[AXItem], bool]] = {
    "h": lambda i: i.role == "heading",
    "f": lambda i: i.role in FORM_FIELD_ROLES,
    "b": lambda i: i.role == "button",
    "k": lambda i: i.role == "link",
    "d": lambda i: i.role in LANDMARK_ROLES,
    "g": lambda i: i.role in ("image", "img"),
}


def describe(item: AXItem) -> str:
    """What a screen reader would say for this item."""
    role = item.role
    word = ROLE_WORDS.get(role, role)
    name = item.name
    s = item.states

    if role == "StaticText":
        return name
    if role == "heading":
        return f"{name or 'blank'}, heading, level {item.level or '?'}"
    if role in LANDMARK_ROLES:
        return f"{name + ', ' if name else ''}{word} landmark"
    if role == "list":
        n = item.child_count
        return f"list with {n} item{'s' if n != 1 else ''}"
    if role in ("alert", "status"):
        return f"{name}, {word}" if name else word
    if role == "link":
        return f"link, {name}" if name else "link"

    parts = [name] if name else []
    parts.append(word)
    if role in ("checkbox", "switch", "radio"):
        checked = s.get("checked")
        if role == "radio":
            parts.append("checked" if checked in (True, "true") else "not checked")
        elif checked == "mixed":
            parts.append("half checked")
        else:
            parts.append("checked" if checked in (True, "true") else "not checked")
    if s.get("expanded") is not None and role not in ("checkbox", "radio"):
        parts.append("expanded" if s["expanded"] in (True, "true") else "collapsed")
    if s.get("required"):
        parts.append("required")
    if s.get("invalid") and s["invalid"] != "false":
        parts.append("invalid entry")
    if s.get("disabled"):
        parts.append("unavailable")
    if s.get("selected") and role != "radio":
        parts.append("selected")
    if s.get("readonly"):
        parts.append("read only")
    if role in ("textbox", "searchbox", "combobox", "spinbutton", "document"):
        value = item.value if len(item.value) <= 80 else item.value[:80] + "..."
        parts.append(value if value else "blank")
    return ", ".join(parts)


# --- speech output ---------------------------------------------------------

@dataclass
class Utterance:
    t: float  # simulated seconds since session start
    text: str
    kind: str  # "speech", "user", "note"


class Speech:
    """Base speech sink. Subclass to send speech somewhere real."""

    def say(self, text: str) -> None:
        pass


class PrintSpeech(Speech):
    def say(self, text: str) -> None:
        print(f"  \N{SPEAKER WITH THREE SOUND WAVES} {text}")


class SpeechDispatcherSpeech(Speech):
    """Speak through speech-dispatcher (what Orca uses on Linux)."""

    def __init__(self, rate: int = 0):
        if not shutil.which("spd-say"):
            raise RuntimeError("spd-say not found; install speech-dispatcher")
        self.rate = rate

    def say(self, text: str) -> None:
        subprocess.run(["spd-say", "-w", "-r", str(self.rate), text], check=False)


class Pyttsx3Speech(Speech):
    """Offline TTS via pyttsx3 (SAPI5 on Windows, NSSpeech on macOS, eSpeak on Linux)."""

    def __init__(self, words_per_minute: int = 200):
        import pyttsx3

        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", words_per_minute)

    def say(self, text: str) -> None:
        self.engine.say(text)
        self.engine.runAndWait()


# --- the screen reader -----------------------------------------------------

class VirtualScreenReader:
    def __init__(self, backend, speech: Speech | None = None, clock=None):
        self.backend = backend
        self.speech = speech or Speech()
        self.clock = clock
        self.transcript: list[Utterance] = []
        self.title = ""
        self.items: list[AXItem] = []
        self.cursor = -1
        self._heard_live: set[str] = set()
        self.refresh()
        self._heard_live = self._live_texts()

    def _now(self) -> float:
        return self.clock.elapsed if self.clock else 0.0

    def say(self, text: str) -> str:
        self.transcript.append(Utterance(self._now(), text, "speech"))
        self.speech.say(text)
        return text

    def note(self, text: str, kind: str = "user") -> None:
        self.transcript.append(Utterance(self._now(), text, kind))

    # --- tree management -----------------------------------------------

    def refresh(self) -> None:
        current = self.items[self.cursor].backend_id if 0 <= self.cursor < len(self.items) else None
        self.title, self.items = self.backend.snapshot()
        self.cursor = self._index_of(current) if current is not None else -1

    def _index_of(self, backend_id: int | None) -> int:
        for idx, item in enumerate(self.items):
            if backend_id is not None and item.backend_id == backend_id:
                return idx
        return -1

    def _live_texts(self) -> set[str]:
        return {i.name for i in self.items if i.live and i.role == "StaticText" and i.name}

    def announce_live_changes(self) -> list[str]:
        """Speak new text in alert/status/aria-live regions, as real SRs do."""
        new = [t for t in (i.name for i in self.items if i.live and i.role == "StaticText")
               if t and t not in self._heard_live]
        self._heard_live = self._live_texts()
        return [self.say(t) for t in new]

    def read_title(self) -> str:
        return self.say(self.title or "untitled document")

    def read_window(self) -> list[str]:
        """What desktop screen readers say when a window or dialog opens:
        its title, then its text (e.g. a message box's message)."""
        spoken = [self.read_title()]
        spoken += [self.say(i.name) for i in self.items if i.role == "StaticText" and i.name]
        return spoken

    # --- browse mode ---------------------------------------------------

    def current(self) -> AXItem | None:
        return self.items[self.cursor] if 0 <= self.cursor < len(self.items) else None

    def move(self, predicate: Callable[[AXItem], bool] | None = None,
             backwards: bool = False) -> tuple[AXItem | None, bool]:
        """Move the virtual cursor to the next matching item.

        Returns (item, wrapped). Like NVDA, when there is nothing further it
        wraps to the start and says so.
        """
        n = len(self.items)
        if n == 0:
            self.say("blank")
            return None, False
        step = -1 if backwards else 1
        start = self.cursor
        wrapped = False
        for k in range(1, n + 1):
            idx = start + step * k
            if idx >= n or idx < 0:
                wrapped = True
            idx %= n
            if predicate is None or predicate(self.items[idx]):
                self.cursor = idx
                if wrapped:
                    self.say("wrapping to top" if not backwards else "wrapping to bottom")
                self.say(describe(self.items[idx]))
                return self.items[idx], wrapped
        self.say("none found")
        return None, wrapped

    def quick_nav(self, key: str) -> tuple[AXItem | None, bool]:
        return self.move(QUICK_NAV[key.lower()], backwards=key.isupper())

    def say_all(self) -> list[str]:
        """Read the whole page from the top (NVDA+Down arrow)."""
        self.cursor = -1
        return [self.say(describe(i)) for i in self.items]

    def activate_current(self) -> None:
        """Put focus on the item under the virtual cursor (entering it)."""
        item = self.current()
        if item is not None:
            self.backend.focus(item)

    # --- focus mode ----------------------------------------------------

    def announce_focus(self) -> AXItem | None:
        item = self.backend.focused()
        if item is None:
            self.say(self.title or "document")
            return None
        idx = self._index_of(item.backend_id)
        if idx >= 0:
            self.cursor = idx
            item = self.items[idx]  # the snapshot copy has live/list info
        self.say(describe(item))
        return item
