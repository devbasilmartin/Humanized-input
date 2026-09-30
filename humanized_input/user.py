"""A simulated screen reader user.

The important rule: the user never uses CSS selectors or the DOM. They only
know what the screen reader has said to them. To fill in "Email" they
navigate (in the style their profile says) until they *hear* something that
sounds like the Email field. If the field has no accessible name, a real
user may never find it, and neither will this one. That is the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ax import FORM_FIELD_ROLES, AXItem
from .backends import Backend
from .pointer import PointerEvent, PointerPlanner, click_landed
from .profiles import Profile
from .screen_reader import VirtualScreenReader, describe
from .timing import Clock, Humanizer, VirtualClock

# Which quick-nav key an expert would press to look for each kind of thing.
_QUICKNAV_KEY = {"field": "f", "button": "b", "link": "k", "heading": "h"}
_KIND_ROLES = {
    "field": FORM_FIELD_ROLES | {"document"},  # document: e.g. Notepad's text area
    "button": {"button"},
    "link": {"link"},
    "heading": {"heading"},
    "any": None,
}


@dataclass
class GoalResult:
    goal: str
    ok: bool
    steps: int
    seconds: float
    note: str = ""


@dataclass
class SessionReport:
    profile: str
    goals: list[GoalResult] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def succeeded(self) -> bool:
        return all(g.ok for g in self.goals)

    def summary(self) -> str:
        lines = [f"Profile {self.profile!r}: {self.elapsed:.1f}s simulated"]
        for g in self.goals:
            mark = "ok  " if g.ok else "FAIL"
            extra = f"  ({g.note})" if g.note else ""
            lines.append(f"  [{mark}] {g.goal}: {g.steps} steps, {g.seconds:.1f}s{extra}")
        return "\n".join(lines)


class SimulatedUser:
    def __init__(self, backend: Backend, profile: Profile, speech=None, clock: Clock | None = None):
        """`backend` is a Backend (browser or Windows desktop). A Playwright
        page is also accepted and wrapped in a BrowserBackend."""
        if not isinstance(backend, Backend):
            from .backends.browser import BrowserBackend

            backend = BrowserBackend(backend)
        self.backend = backend
        self.profile = profile
        self.clock = clock or VirtualClock()
        self.human = Humanizer(profile)
        self.pointer = PointerPlanner(self.human)
        self.sr = VirtualScreenReader(backend, speech=speech, clock=self.clock)
        self.report = SessionReport(profile=profile.name)

    # --- low-level human actions ---------------------------------------

    def _listen(self, utterance: str, interesting: bool = False) -> None:
        self.clock.sleep(self.human.listen_time(utterance, full=interesting))
        self.clock.sleep(self.human.reaction())

    def _think(self) -> None:
        self.clock.sleep(self.human.think())

    def _press(self, key: str) -> None:
        self.sr.note(f"presses {key}")
        self.backend.press(key, self.human.key_hold())

    def _after_page_change(self) -> None:
        self.backend.settle()
        old_title = self.sr.title
        self.sr.refresh()
        if self.sr.title != old_title:
            if self.backend.reads_new_window_text:
                for text in self.sr.read_window():
                    self._listen(text, interesting=True)
            else:
                self.sr.read_title()
        for text in self.sr.announce_live_changes():
            self._listen(text, interesting=True)

    # --- finding things by ear -----------------------------------------

    @staticmethod
    def _matches(item: AXItem | None, name: str, roles) -> bool:
        if item is None:
            return False
        if roles is not None and item.role not in roles:
            return False
        return name.casefold() in item.name.casefold()

    def find(self, name: str, kind: str = "any") -> tuple[AXItem | None, int, str]:
        """Navigate until the user hears `name`. Returns (item, steps, note).

        If their usual strategy fails, users fall back to reading the page
        line by line from the top, like real users do.
        """
        strategy = self.profile.navigation
        if strategy == "quicknav" and not self.backend.browse_mode:
            strategy = "tab"  # desktop apps have no quick-nav keys; experts Tab
        item, steps, note = self._search(name, kind, strategy, self.profile.max_steps)
        if item is None and strategy != "linear":
            self.sr.note("falls back to reading line by line from the top")
            self.sr.cursor = -1
            item, more, note = self._search(name, kind, "linear", len(self.sr.items))
            steps += more
            if item is not None:
                note = f"{strategy} failed; " + (note or "found by reading line by line")
        return item, steps, note

    def _search(self, name: str, kind: str, strategy: str,
                max_steps: int) -> tuple[AXItem | None, int, str]:
        roles = _KIND_ROLES[kind]
        self.sr.note(f"looking for {kind} {name!r} using {strategy}")
        seen: set[int | None] = set()
        prev: AXItem | None = None

        for step in range(1, max_steps + 1):
            if strategy == "tab":
                self._press("Tab")
                item = self.sr.announce_focus()
            elif strategy == "quicknav" and kind in _QUICKNAV_KEY:
                item, _ = self.sr.quick_nav(_QUICKNAV_KEY[kind])
            else:  # linear, or quicknav with nothing better to press
                item, _ = self.sr.move()

            heard = describe(item) if item else ""
            if self._matches(item, name, roles):
                self._listen(heard, interesting=True)
                return item, step, ""

            # A person reading line by line can guess that an unlabelled field
            # belongs to the text just before it. Screen reader users do this
            # all the time; it is still an accessibility bug.
            if (strategy == "linear" and item is not None and roles is not None
                    and item.role in roles and not item.name
                    and prev is not None and prev.role == "StaticText"
                    and name.casefold() in prev.name.casefold()):
                self._listen(heard, interesting=True)
                return item, step, "guessed from nearby text: field has no accessible name"

            self._listen(heard)
            key = item.backend_id if item else None
            if key in seen and strategy != "linear":
                return None, step, "never heard it"  # went all the way round
            seen.add(key)
            prev = item
        return None, max_steps, "never heard it"

    def _goal(self, label: str, name: str, kind: str, action) -> bool:
        start = self.clock.elapsed
        self._think()
        item, steps, note = self.find(name, kind)
        ok = item is not None
        if ok:
            self.sr.activate_current()  # browse mode -> focus the item (no-op after Tab)
            self._think()
            action(item)
            self._after_page_change()
        self.report.goals.append(GoalResult(label, ok, steps, self.clock.elapsed - start, note))
        self.report.elapsed = self.clock.elapsed
        return ok

    # --- goals (what a test script calls) ------------------------------

    def _type_text(self, text: str) -> None:
        self.sr.note(f"types {text!r}")
        for stroke in self.human.plan_typing(text):
            self.clock.sleep(stroke.delay_before)
            self.backend.press(stroke.key, self.human.key_hold())

    def fill(self, name: str, text: str) -> bool:
        return self._goal(f"fill {name!r}", name, "field", lambda _i: self._type_text(text))

    def type(self, text: str) -> None:
        """Type into whatever has focus now, without looking for anything."""
        self._think()
        self._type_text(text)
        self.report.elapsed = self.clock.elapsed

    def shortcut(self, keys: str) -> None:
        """Press a key or combination, e.g. "Control+s", "Alt+F4", "Escape",
        then listen to whatever changed (a dialog opening, say)."""
        self._think()
        self._press(keys)
        self._after_page_change()
        self.report.elapsed = self.clock.elapsed

    def check(self, name: str) -> bool:
        return self._goal(f"check {name!r}", name, "field", lambda _i: self._press("Space"))

    def press(self, name: str, kind: str = "button") -> bool:
        return self._goal(f"activate {kind} {name!r}", name, kind, lambda _i: self._press("Enter"))

    # --- mouse ------------------------------------------------------------

    def _run_pointer(self, events: list[PointerEvent]) -> None:
        for ev in events:
            self.clock.sleep(ev.delay_before)
            if ev.kind == "move":
                self.backend.mouse_move(ev.x, ev.y)
            else:
                self.backend.mouse_button(ev.button, ev.kind == "down")

    def click(self, name: str, kind: str = "button", button: str = "left",
              clicks: int = 1) -> bool:
        """Click something with the mouse, like a sighted or low-vision user.

        Unlike the keyboard goals, the user finds the target by looking at
        the screen, not by listening, so it only needs to be on screen. The
        goal fails if the item has no on-screen box, or if the press or
        release lands outside it (e.g. a tremor on a tiny target).
        """
        start = self.clock.elapsed
        self._think()  # visual search
        self.sr.refresh()
        roles = _KIND_ROLES[kind]
        item = next((i for i in self.sr.items if self._matches(i, name, roles)), None)
        box = self.backend.bounds(item) if item is not None else None
        ok, note = False, ""
        if item is None:
            note = "not on screen"
        elif box is None:
            note = "has no on-screen box to click"
        else:
            events = self.pointer.plan_click(self.backend.mouse_position(), box, button, clicks)
            self.sr.note(f"moves the mouse to {name!r} and clicks")
            self._run_pointer(events)
            ok = click_landed(events, box)
            if not ok:
                note = f"missed the target ({box.width:.0f}x{box.height:.0f} px)"
            self._after_page_change()
        self.report.goals.append(GoalResult(f"click {kind} {name!r}", ok, 1, self.clock.elapsed - start, note))
        self.report.elapsed = self.clock.elapsed
        return ok

    def read_page(self) -> list[str]:
        """Listen to the whole page once, top to bottom."""
        self.sr.read_title()
        spoken = self.sr.say_all()
        for text in spoken:
            self._listen(text, interesting=True)
        self.report.elapsed = self.clock.elapsed
        return spoken
