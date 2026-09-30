"""Real Windows keystrokes and mouse input through the SendInput API.

SendInput puts key events into the same input stream a physical keyboard
uses, so the focused app (and a running screen reader such as NVDA) sees
them as ordinary key presses. Each key is sent as separate down and up
events so we can hold it for a human-like duration.

Text characters are sent as Unicode (KEYEVENTF_UNICODE), which works in
nearly every app regardless of keyboard layout. Named keys (Tab, Enter,
arrows...) are sent as virtual-key codes.

Mouse moves are sent as absolute positions on the virtual desktop (all
monitors), normalised to 0..65535 as SendInput requires. pointer.py plans
the path; this module only delivers each point.

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

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSE_BUTTON_FLAGS = {  # (down, up)
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
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


def normalise_point(x: int, y: int, desktop: tuple[int, int, int, int]) -> tuple[int, int]:
    """Map a screen pixel to SendInput's 0..65535 absolute coordinates.

    `desktop` is the virtual screen as (left, top, width, height); the left
    and top can be negative when a monitor sits left of or above the primary.
    Pure function, so it can be tested on any OS.
    """
    left, top, width, height = desktop
    x = min(max(x, left), left + width - 1)
    y = min(max(y, top), top + height - 1)
    return (round((x - left) * 65535 / max(width - 1, 1)),
            round((y - top) * 65535 / max(height - 1, 1)))


def _input_types():
    """The ctypes structures SendInput takes."""
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):
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

    send_input = ctypes.windll.user32.SendInput
    send_input.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    send_input.restype = wintypes.UINT
    return INPUT, KEYBDINPUT, MOUSEINPUT, send_input


class _SendInput:
    def __init__(self):
        if sys.platform != "win32":
            raise OSError(f"{type(self).__name__} only works on Windows")
        self._INPUT, self._KEYBDINPUT, self._MOUSEINPUT, self._send_input = _input_types()

    def _send_array(self, arr, n: int) -> None:
        sent = self._send_input(n, arr, ctypes.sizeof(self._INPUT))
        if sent != n:
            raise OSError(ctypes.WinError())


class SendInputKeyboard(_SendInput):
    """Sends key events to the foreground window. Windows only."""

    def _send(self, events: list[KeyEvent]) -> None:
        if not events:
            return
        arr = (self._INPUT * len(events))()
        for i, ev in enumerate(events):
            arr[i].type = INPUT_KEYBOARD
            arr[i].u.ki = self._KEYBDINPUT(ev.vk, ev.scan, ev.flags, 0, 0)
        self._send_array(arr, len(events))

    def press(self, key: str, hold: float = 0.0) -> None:
        down, up = plan_key(key)
        self._send(down)
        if hold > 0:
            time.sleep(hold)
        self._send(up)


class SendInputMouse(_SendInput):
    """Moves the real pointer and presses real buttons. Windows only."""

    def __init__(self):
        super().__init__()
        user32 = ctypes.windll.user32
        # Without DPI awareness, Windows scales our coordinates on high-DPI
        # screens and the pointer lands in the wrong place.
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
        self._user32 = user32

    def desktop(self) -> tuple[int, int, int, int]:
        m = self._user32.GetSystemMetrics
        return (m(SM_XVIRTUALSCREEN), m(SM_YVIRTUALSCREEN),
                m(SM_CXVIRTUALSCREEN), m(SM_CYVIRTUALSCREEN))

    def _send(self, dx: int, dy: int, flags: int) -> None:
        arr = (self._INPUT * 1)()
        arr[0].type = INPUT_MOUSE
        arr[0].u.mi = self._MOUSEINPUT(dx, dy, 0, flags, 0, 0)
        self._send_array(arr, 1)

    def position(self) -> tuple[int, int]:
        from ctypes import wintypes

        pt = wintypes.POINT()
        self._user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def move(self, x: int, y: int) -> None:
        dx, dy = normalise_point(x, y, self.desktop())
        self._send(dx, dy, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)

    def button(self, button: str, down: bool) -> None:
        if button not in MOUSE_BUTTON_FLAGS:
            raise ValueError(f"Unknown mouse button {button!r}")
        self._send(0, 0, MOUSE_BUTTON_FLAGS[button][0 if down else 1])
