"""Windows desktop apps via UI Automation (UIA).

UI Automation is the Windows accessibility API. Narrator is built on it, and
NVDA and JAWS use it for modern apps (WinForms, WPF, UWP/WinUI, Office,
Chromium-based apps, File Explorer...). Every element exposes a control type,
a Name, and "patterns" for its state: Toggle (check boxes), Value (edit
fields), SelectionItem (radio buttons, list items), ExpandCollapse, etc.

This backend walks that tree for the foreground window and turns each element
into the same AXItem the browser backend produces, so the rest of the project
(virtual screen reader, simulated user, audit) works unchanged.

Requires Windows and `pip install uiautomation` (a thin Python wrapper over
the COM interface).

Conversion code takes duck-typed "controls" (anything with the same
attributes as uiautomation.Control) so it can be unit-tested with fakes.
"""

from __future__ import annotations

import time

from ..ax import ANNOUNCED_CONTAINERS, LEAF_ROLES, AXItem
from . import Backend

# UIA control type -> the role names used throughout this project.
CONTROL_ROLES = {
    "ButtonControl": "button",
    "SplitButtonControl": "button",
    "CheckBoxControl": "checkbox",
    "RadioButtonControl": "radio",
    "EditControl": "textbox",
    "DocumentControl": "document",
    "ComboBoxControl": "combobox",
    "HyperlinkControl": "link",
    "TextControl": "StaticText",
    "ImageControl": "image",
    "ListControl": "list",
    "ListItemControl": "listitem",
    "DataItemControl": "listitem",
    "TreeControl": "tree",
    "TreeItemControl": "treeitem",
    "TabControl": "tablist",
    "TabItemControl": "tab",
    "MenuBarControl": "menubar",
    "MenuControl": "menu",
    "MenuItemControl": "menuitem",
    "ToolBarControl": "toolbar",
    "SliderControl": "slider",
    "SpinnerControl": "spinbutton",
    "ProgressBarControl": "progressbar",
    "StatusBarControl": "status",
    "GroupControl": "group",
    "WindowControl": "dialog",
    "TableControl": "table",
    "DataGridControl": "table",
}
# Window chrome a screen reader does not read as content.
SKIP_TYPES = {"TitleBarControl", "ScrollBarControl", "ThumbControl", "SeparatorControl",
              "ToolTipControl"}
UIA_LEAF_ROLES = LEAF_ROLES | {"listitem", "treeitem", "document", "menuitem", "tab"}
UIA_CONTAINERS = ANNOUNCED_CONTAINERS | {"tablist", "tree", "menubar", "menu", "toolbar",
                                         "group"}

# Pattern and property ids (from UIAutomationClient.h).
VALUE_PATTERN = 10002
EXPAND_COLLAPSE_PATTERN = 10005
SELECTION_ITEM_PATTERN = 10010
TOGGLE_PATTERN = 10015
IS_REQUIRED_FOR_FORM_PROPERTY = 30025
LIVE_SETTING_PROPERTY = 30135
HEADING_LEVEL_PROPERTY = 30173  # Windows 10 1803+
HEADING_LEVEL_NONE = 80050

MAX_VALUE_CHARS = 200


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:  # COM errors happen when elements vanish mid-read
        return default


def item_from_control(ctrl) -> AXItem | None:
    """Convert one UIA control to an AXItem (None for chrome to skip)."""
    type_name = _safe(lambda: ctrl.ControlTypeName, "")
    if type_name in SKIP_TYPES:
        return None
    role = CONTROL_ROLES.get(type_name, "generic")
    states: dict[str, object] = {}

    if not _safe(lambda: ctrl.IsEnabled, True):
        states["disabled"] = True
    if _safe(lambda: ctrl.GetPropertyValue(IS_REQUIRED_FOR_FORM_PROPERTY), False):
        states["required"] = True

    toggle = _safe(lambda: ctrl.GetPattern(TOGGLE_PATTERN))
    if toggle is not None and role in ("checkbox", "button", "menuitem", "listitem", "treeitem"):
        state = _safe(lambda: toggle.ToggleState, 0)
        states["checked"] = {0: False, 1: True, 2: "mixed"}.get(state, False)
        if role == "button":
            role = "switch" if type_name == "ButtonControl" else role

    sel = _safe(lambda: ctrl.GetPattern(SELECTION_ITEM_PATTERN))
    if sel is not None and _safe(lambda: sel.IsSelected, False):
        states["checked" if role == "radio" else "selected"] = True

    exp = _safe(lambda: ctrl.GetPattern(EXPAND_COLLAPSE_PATTERN))
    if exp is not None:
        # 0 collapsed, 1 expanded, 2 partially expanded, 3 leaf (no children)
        st = _safe(lambda: exp.ExpandCollapseState, 3)
        if st in (0, 1, 2):
            states["expanded"] = st != 0

    value = ""
    val = _safe(lambda: ctrl.GetPattern(VALUE_PATTERN))
    if val is not None:
        value = str(_safe(lambda: val.Value, "") or "")
        if len(value) > MAX_VALUE_CHARS:
            value = value[:MAX_VALUE_CHARS] + "..."
        if _safe(lambda: val.IsReadOnly, False) and role in ("textbox", "document"):
            states["readonly"] = True

    level = None
    raw_level = _safe(lambda: ctrl.GetPropertyValue(HEADING_LEVEL_PROPERTY))
    if isinstance(raw_level, int) and HEADING_LEVEL_NONE < raw_level <= HEADING_LEVEL_NONE + 9:
        level = raw_level - HEADING_LEVEL_NONE
        role = "heading"

    live = (_safe(lambda: ctrl.GetPropertyValue(LIVE_SETTING_PROPERTY), 0) or 0) > 0

    runtime_id = _safe(lambda: tuple(ctrl.GetRuntimeId()))
    return AXItem(
        role=role,
        name=(_safe(lambda: ctrl.Name, "") or "").strip(),
        value=value,
        level=level,
        states=states,
        backend_id=runtime_id,
        focusable=bool(_safe(lambda: ctrl.IsKeyboardFocusable, False)),
        live=live or role == "status",
    )


def flatten_uia(root, max_items: int = 3000, max_depth: int = 40):
    """Walk a UIA subtree in reading order, like NVDA's object navigation.

    Returns (title, items, controls_by_id).
    """
    title = (_safe(lambda: root.Name, "") or "").strip()
    items: list[AXItem] = []
    controls: dict[object, object] = {}

    def walk(ctrl, depth: int, live: bool):
        if len(items) >= max_items or depth > max_depth:
            return
        if _safe(lambda: ctrl.IsOffscreen, False):
            return
        item = item_from_control(ctrl)
        if item is None:
            return
        live = live or item.live
        item.live = live
        role = item.role
        if item.backend_id is not None:
            controls[item.backend_id] = ctrl

        if role in UIA_LEAF_ROLES:
            if role != "StaticText" or item.name:
                items.append(item)
            return
        if role in UIA_CONTAINERS and (item.name or role not in ("group", "toolbar", "dialog")):
            if role == "list":
                item.child_count = sum(
                    1 for c in _safe(ctrl.GetChildren, []) or []
                    if _safe(lambda: c.ControlTypeName) in ("ListItemControl", "DataItemControl")
                )
            items.append(item)
        for child in _safe(ctrl.GetChildren, []) or []:
            walk(child, depth + 1, live)

    for child in _safe(root.GetChildren, []) or []:
        walk(child, 1, False)
    return title, items, controls


class WindowsUIABackend(Backend):
    browse_mode = False
    reads_new_window_text = True
    kind = "desktop"

    def __init__(self, window=None, keyboard=None, settle_s: float = 0.15, mouse=None):
        """`window` is a uiautomation control for the app's main window. The
        backend always reads the current foreground window of that app, so it
        follows dialogs and message boxes the app opens."""
        import uiautomation as auto

        from .win_input import SendInputKeyboard, SendInputMouse

        self.auto = auto
        self._uia_thread = auto.UIAutomationInitializerInThread()
        self.window = window
        self.keyboard = keyboard or SendInputKeyboard()
        self.mouse = mouse or SendInputMouse()
        self.settle_s = settle_s
        self._controls: dict[object, object] = {}

    # --- windows -------------------------------------------------------

    def _process_id(self, ctrl):
        return _safe(lambda: ctrl.ProcessId)

    def current_window(self):
        fg = _safe(self.auto.GetForegroundControl)
        if self.window is None:
            return fg
        if fg is not None and self._process_id(fg) == self._process_id(self.window):
            return fg  # the app's own dialog or main window
        return self.window

    def bring_to_front(self) -> None:
        if self.window is not None:
            _safe(self.window.SetActive)
            _safe(self.window.SetFocus)
            time.sleep(0.2)

    # --- Backend interface ----------------------------------------------

    def snapshot(self):
        root = self.current_window()
        if root is None:
            return "", []
        title, items, self._controls = flatten_uia(root)
        return title, items

    def focused(self) -> AXItem | None:
        ctrl = _safe(self.auto.GetFocusedControl)
        if ctrl is None:
            return None
        item = item_from_control(ctrl)
        if item is None or item.role == "dialog":
            return None
        return item

    def focus(self, item: AXItem) -> None:
        ctrl = self._controls.get(item.backend_id)
        if ctrl is not None:
            _safe(ctrl.SetFocus)

    def _ensure_front(self) -> None:
        if self.window is not None:
            fg = _safe(self.auto.GetForegroundControl)
            if fg is None or self._process_id(fg) != self._process_id(self.window):
                self.bring_to_front()  # never type or click into some other app

    def press(self, key: str, hold: float = 0.0) -> None:
        self._ensure_front()
        self.keyboard.press(key, hold)

    # --- mouse -----------------------------------------------------------

    def bounds(self, item: AXItem):
        from ..pointer import Rect

        ctrl = self._controls.get(item.backend_id)
        r = _safe(lambda: ctrl.BoundingRectangle) if ctrl is not None else None
        if r is None or r.right <= r.left or r.bottom <= r.top:
            return None
        return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)

    def mouse_position(self) -> tuple[float, float]:
        return self.mouse.position()

    def mouse_move(self, x: int, y: int) -> None:
        self.mouse.move(x, y)

    def mouse_button(self, button: str, down: bool) -> None:
        if down:
            self._ensure_front()
        self.mouse.button(button, down)

    def settle(self) -> None:
        time.sleep(self.settle_s)
