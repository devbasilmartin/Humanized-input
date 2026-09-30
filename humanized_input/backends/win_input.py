"""Real Windows keystrokes through the SendInput API.

SendInput puts key events into the same input stream a physical keyboard
uses, so the focused app (and a running screen reader such as NVDA) sees
them as ordinary key presses. Each key is sent as separate down and up
events so we can hold it for a human-like duration.

Text characters are sent as Unicode (KEYEVENTF_UNICODE), which works in
nearly every app regardless of keyboard layout. Named keys (Tab, Enter,
arrows...) are sent as virtual-key codes.

Limitations worth knowing:
- Input goes to whichever window is in the foreground.
- Windows blocks input from a normal process into an elevated (admin)
  window (UIPI). Run Python elevated to drive elevated apps.
- The lock screen and UAC prompts cannot be driven at all.
"""

from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK = {
    "Backspace": 0x08, "Tab": 0x09, "Enter": 0x0D, "Shift": 0x10, "Control": 0x11,
    "Alt": 0x12, "Pause": 0x13, "CapsLock": 0x14, "Escape": 0x1B, "Space": 0x20,
    "PageUp": 0x21, "PageDown": 0x22, "End": 0x23, "Home": 0x24,
    "ArrowLeft": 0x25, "ArrowUp": 0x26, "ArrowRight": 0x27, "ArrowDown": 0x28,
    "Insert": 0x2D, "Delete": 0x2E, "Meta": 0x5B, "ContextMenu": 0x5D,
    "NumpadAdd": 0x6B, "NumpadSubtract": 0x6D,
    **{f"F{n}": 0x6F + n for n in range(1, 13)},
}
ALIASES = {
    "Ctrl": "Control", "Win": "Meta", "Esc": "Escape", "Return": "Enter",
    "Left": "ArrowLeft", "Right": "ArrowRight", "Up": "ArrowUp", "Down": "ArrowDown",
    "Del": "Delete", "Ins": "Insert", "Apps": "ContextMenu",
}
# Keys that live on the "extended" part of the keyboard; they need a flag or
# some apps confuse them with the numeric keypad.
EXTENDED = {"Insert", "Delete", "Home", "End", "PageUp", "PageDown", "ArrowLeft",
            "ArrowUp", "ArrowRight", "ArrowDown", "Meta", "ContextMenu"}


@dataclass(frozen=True)
class KeyEvent:
    vk: int  # virtual-key code, 0 for Unicode events
    scan: int  # UTF-16 code unit for Unicode events
    flags: int

    @property
    def is_up(self) -> bool:
        return bool(self.flags & KEYEVENTF_KEYUP)


def _named_down_up(name: str) -> tuple[KeyEvent, KeyEvent]:
    name = ALIASES.get(name, name)
    if name not in VK:
        raise ValueError(f"Unknown key name {name!r}")
    flags = KEYEVENTF_EXTENDEDKEY if name in EXTENDED else 0
    return KeyEvent(VK[name], 0, flags), KeyEvent(VK[name], 0, flags | KEYEVENTF_KEYUP)


def plan_key(key: str) -> tuple[list[KeyEvent], list[KeyEvent]]:
    """Split a key spec into (events to press, events to release).

    Pure function, so it can be tested on any OS. Examples:
    "a" -> Unicode 'a'; "Tab" -> VK_TAB; "Shift+Tab" -> Shift down, Tab down,
    then Tab up, Shift up; " " and "Space" -> VK_SPACE.
    """
    if key == " ":
        key = "Space"
    if len(key) > 1 and "+" in key and not key.endswith("++"):
        *mods, main = key.split("+")
    elif key.endswith("++") and len(key) > 2:
        *mods, main = key[:-2].split("+") + ["+"]
    else:
        mods, main = [], key

    down: list[KeyEvent] = []
    up: list[KeyEvent] = []
    for mod in mods:
        d, u = _named_down_up(mod)
        down.append(d)
        up.insert(0, u)

    if len(main) == 1 and not mods:
        # A typed character: send its UTF-16 code units as Unicode input.
        units = main.encode("utf-16-le")
        codes = [int.from_bytes(units[i:i + 2], "little") for i in range(0, len(units), 2)]
        down += [KeyEvent(0, c, KEYEVENTF_UNICODE) for c in codes]
        up = [KeyEvent(0, c, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP) for c in codes] + up
    elif len(main) == 1:
        # Shortcut like Ctrl+a: use the layout's virtual key, not Unicode.
        vk = _vk_for_char(main)
        down.append(KeyEvent(vk, 0, 0))
        up.insert(0, KeyEvent(vk, 0, KEYEVENTF_KEYUP))
    else:
        d, u = _named_down_up(main)
        down.append(d)
        up.insert(0, u)
    return down, up


def _vk_for_char(ch: str) -> int:
    if ch.isalnum() and ch.isascii():
        return ord(ch.upper())
    if sys.platform == "win32":
        res = ctypes.windll.user32.VkKeyScanW(ord(ch))
        if res != -1:
            return res & 0xFF
    raise ValueError(f"No virtual key for {ch!r}")


class SendInputKeyboard:
    """Sends key events to the foreground window. Windows only."""

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("SendInputKeyboard only works on Windows")
        from ctypes import wintypes

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class MOUSEINPUT(ctypes.Structure):  # only here to size the union correctly
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                        ("wParamH", wintypes.WORD)]

        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        self._INPUT = INPUT
        self._KEYBDINPUT = KEYBDINPUT
        self._send_input = ctypes.windll.user32.SendInput
        self._send_input.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self._send_input.restype = wintypes.UINT

    def _send(self, events: list[KeyEvent]) -> None:
        if not events:
            return
        arr = (self._INPUT * len(events))()
        for i, ev in enumerate(events):
            arr[i].type = INPUT_KEYBOARD
            arr[i].u.ki = self._KEYBDINPUT(ev.vk, ev.scan, ev.flags, 0, 0)
        sent = self._send_input(len(events), arr, ctypes.sizeof(self._INPUT))
        if sent != len(events):
            raise OSError(ctypes.WinError())

    def press(self, key: str, hold: float = 0.0) -> None:
        down, up = plan_key(key)
        self._send(down)
        if hold > 0:
            time.sleep(hold)
        self._send(up)
