"""Tests for the Windows desktop pieces that run on any OS.

The UIA conversion is tested with fake controls shaped like
uiautomation.Control, key planning is a pure function, and the desktop user
flow runs against a tiny in-memory "app".
"""

import pytest

from humanized_input import BUILTIN_PROFILES, SimulatedUser, audit
from humanized_input.ax import AXItem
from humanized_input.backends import Backend
from humanized_input.backends.win_input import (
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    VK,
    plan_key,
)
from humanized_input.backends.windows_uia import (
    SELECTION_ITEM_PATTERN,
    TOGGLE_PATTERN,
    VALUE_PATTERN,
    flatten_uia,
)
from humanized_input.screen_reader import describe

# --- SendInput key planning -------------------------------------------------


def test_character_is_sent_as_unicode():
    down, up = plan_key("a")
    assert [(e.vk, e.scan, e.flags) for e in down] == [(0, ord("a"), KEYEVENTF_UNICODE)]
    assert up[0].is_up


def test_emoji_uses_both_utf16_surrogates():
    down, up = plan_key("\N{GRINNING FACE}")
    assert [e.scan for e in down] == [0xD83D, 0xDE00]
    assert len(up) == 2 and all(e.is_up for e in up)


def test_combination_presses_modifiers_first_and_releases_them_last():
    down, up = plan_key("Shift+Tab")
    assert [e.vk for e in down] == [VK["Shift"], VK["Tab"]]
    assert [e.vk for e in up] == [VK["Tab"], VK["Shift"]]
    assert all(e.flags & KEYEVENTF_KEYUP for e in up)


def test_shortcut_letter_uses_virtual_key():
    down, _ = plan_key("Control+s")
    assert [e.vk for e in down] == [VK["Control"], ord("S")]


def test_space_and_aliases():
    assert plan_key(" ")[0][0].vk == VK["Space"]
    assert plan_key("Esc")[0][0].vk == VK["Escape"]
    assert plan_key("+")[0][0].scan == ord("+")
    with pytest.raises(ValueError):
        plan_key("NotAKey")


# --- UIA tree conversion ------------------------------------------------------


class Pattern:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeControl:
    _next_id = 0

    def __init__(self, type_name, name="", children=(), patterns=None, offscreen=False,
                 focusable=True, enabled=True):
        FakeControl._next_id += 1
        self.ControlTypeName = type_name
        self.Name = name
        self.IsOffscreen = offscreen
        self.IsKeyboardFocusable = focusable
        self.IsEnabled = enabled
        self._children = list(children)
        self._patterns = patterns or {}
        self._id = FakeControl._next_id

    def GetChildren(self):
        return self._children

    def GetPattern(self, pid):
        return self._patterns.get(pid)

    def GetPropertyValue(self, pid):
        return None

    def GetRuntimeId(self):
        return [42, self._id]


def test_flatten_uia_maps_controls_to_screen_reader_items():
    window = FakeControl("WindowControl", "Sign up", children=[
        FakeControl("TitleBarControl", "Sign up", children=[FakeControl("ButtonControl", "Close")]),
        FakeControl("TextControl", "Full name"),
        FakeControl("EditControl", "Full name", patterns={VALUE_PATTERN: Pattern(Value="Ada", IsReadOnly=False)}),
        FakeControl("EditControl", "", patterns={VALUE_PATTERN: Pattern(Value="", IsReadOnly=False)}),
        FakeControl("CheckBoxControl", "I agree", patterns={TOGGLE_PATTERN: Pattern(ToggleState=1)}),
        FakeControl("RadioButtonControl", "Email me",
                    patterns={SELECTION_ITEM_PATTERN: Pattern(IsSelected=True)}),
        FakeControl("PaneControl", children=[FakeControl("ButtonControl", "Create account")]),
        FakeControl("ButtonControl", "Hidden", offscreen=True),
        FakeControl("ListControl", "Books", children=[
            FakeControl("ListItemControl", "Dune"), FakeControl("ListItemControl", "Emma")]),
    ])
    title, items, controls = flatten_uia(window)
    assert title == "Sign up"
    assert [describe(i) for i in items] == [
        "Full name",
        "Full name, edit, Ada",
        "edit, blank",
        "I agree, check box, checked",
        "Email me, radio button, checked",
        "Create account, button",
        "list with 2 items",
        "Dune, list item",
        "Emma, list item",
    ]
    assert all(i.backend_id in controls for i in items)
    rules = [i.rule for i in audit(title, items, kind="desktop")]
    assert rules == ["unnamed-control"]  # no web-only heading/landmark rules


# --- A simulated user in a fake desktop app ------------------------------------


class FakeSignupApp(Backend):
    """In-memory stand-in for the WinForms sign-up window."""

    browse_mode = False
    reads_new_window_text = True
    kind = "desktop"

    def __init__(self):
        self.fields = {"Full name": "", "Email": "", "Card": ""}
        self.checked = False
        self.dialog = None  # (title, message) while a message box is open
        self.focus_index = -1
        self.keys: list[str] = []

    def _controls(self):
        return [
            AXItem("textbox", "Full name", value=self.fields["Full name"], backend_id=1, focusable=True),
            AXItem("textbox", "Email", value=self.fields["Email"], backend_id=2, focusable=True),
            AXItem("textbox", "", value=self.fields["Card"], backend_id=3, focusable=True),
            AXItem("checkbox", "I agree to the terms", states={"checked": self.checked},
                   backend_id=4, focusable=True),
            AXItem("button", "Create account", backend_id=5, focusable=True),
        ]

    def snapshot(self):
        if self.dialog:
            title, msg = self.dialog
            return title, [AXItem("StaticText", msg, backend_id=90), AXItem("button", "OK", backend_id=91)]
        return "Sign up - Example Library", self._controls()

    def focused(self):
        if self.dialog:
            return AXItem("button", "OK", backend_id=91)
        if self.focus_index < 0:
            return None
        return self._controls()[self.focus_index]

    def focus(self, item):
        if item.backend_id and item.backend_id < 90:
            self.focus_index = item.backend_id - 1

    def press(self, key, hold=0.0):
        self.keys.append(key)
        if self.dialog:
            if key in ("Enter", "Escape", "Space"):
                self.dialog = None
            return
        if key == "Tab":
            self.focus_index = (self.focus_index + 1) % 5
            return
        item = self.focused()
        if item is None:
            return
        if item.role == "textbox":
            field = item.name or "Card"
            if key == "Backspace":
                self.fields[field] = self.fields[field][:-1]
            elif len(key) == 1:
                self.fields[field] += key
        elif item.role == "checkbox" and key == "Space":
            self.checked = not self.checked
        elif item.role == "button" and key == "Enter":
            if "@" not in self.fields["Email"]:
                self.dialog = ("Error", "Enter a valid email address.")


@pytest.mark.parametrize("name", list(BUILTIN_PROFILES))
def test_desktop_user_fills_form_with_real_keystrokes(name):
    app = FakeSignupApp()
    user = SimulatedUser(app, BUILTIN_PROFILES[name].with_seed(5))
    assert user.fill("Full name", "Ada Lovelace")
    assert user.fill("Email", "ada@example.org")
    assert user.check("agree")
    assert app.fields == {"Full name": "Ada Lovelace", "Email": "ada@example.org", "Card": ""}
    assert app.checked
    # Only typing and navigation keys reached the app (typos included).
    assert all(len(k) == 1 or k in ("Tab", "Space", "Backspace") for k in app.keys)


def test_expert_uses_tab_on_desktop_and_hears_the_error_dialog():
    app = FakeSignupApp()
    user = SimulatedUser(app, BUILTIN_PROFILES["expert"].with_seed(1))
    user.fill("Email", "oops")
    user.press("Create account")
    assert "Tab" in app.keys
    spoken = [u.text for u in user.sr.transcript if u.kind == "speech"]
    assert spoken[-2:] == ["Error", "Enter a valid email address."]
    user.shortcut("Enter")  # dismiss the message box
    assert app.dialog is None
    assert user.sr.transcript[-1].text == "Sign up - Example Library"


def test_unnamed_desktop_field_found_only_by_guessing():
    app = FakeSignupApp()
    user = SimulatedUser(app, BUILTIN_PROFILES["intermediate"].with_seed(1))
    # The fake app has no visible label text for the card box, so even
    # reading line by line gives no clue: the user never finds it.
    assert not user.fill("Library card", "123")
    assert user.report.goals[-1].note == "never heard it"
