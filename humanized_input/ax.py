"""Reading the browser's accessibility tree.

Screen readers do not read HTML. They read the accessibility tree the browser
builds from it: every node has a role (button, heading, textbox...), a
computed accessible name, and states (checked, required, expanded...). If
something is missing from this tree, a screen reader user cannot find it.

We fetch Chromium's real tree through the Chrome DevTools Protocol, the same
information NVDA, JAWS and Orca get through platform APIs (UIA, IA2, AT-SPI).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Things a screen reader presents as a single item; we do not descend into them.
LEAF_ROLES = {
    "heading", "link", "button", "textbox", "searchbox", "checkbox", "radio",
    "combobox", "listbox", "image", "img", "slider", "spinbutton", "switch",
    "menuitem", "tab", "option", "StaticText", "progressbar",
}
# Containers the screen reader announces on entry, then reads what is inside.
ANNOUNCED_CONTAINERS = {
    "navigation", "main", "banner", "contentinfo", "form", "region",
    "complementary", "search", "list", "alert", "status", "dialog", "table",
}
LANDMARK_ROLES = {
    "navigation", "main", "banner", "contentinfo", "form", "region",
    "complementary", "search",
}
FORM_FIELD_ROLES = {
    "textbox", "searchbox", "checkbox", "radio", "combobox", "listbox",
    "slider", "spinbutton", "switch",
}
INTERACTIVE_ROLES = FORM_FIELD_ROLES | {"link", "button", "menuitem", "tab"}
LIVE_ROLES = {"alert", "status", "log"}

_STATE_PROPS = ("required", "invalid", "disabled", "expanded", "checked",
                "pressed", "selected", "readonly")


@dataclass
class AXItem:
    role: str
    name: str = ""
    value: str = ""
    level: int | None = None
    states: dict[str, object] = field(default_factory=dict)
    backend_id: int | None = None
    focusable: bool = False
    live: bool = False
    child_count: int = 0

    @property
    def is_interactive(self) -> bool:
        return self.role in INTERACTIVE_ROLES

    @property
    def is_form_field(self) -> bool:
        return self.role in FORM_FIELD_ROLES


def _prop_map(node: dict) -> dict[str, object]:
    return {p["name"]: p.get("value", {}).get("value") for p in node.get("properties", [])}


def item_from_node(node: dict, live: bool = False) -> AXItem:
    props = _prop_map(node)
    states = {k: props[k] for k in _STATE_PROPS if k in props and props[k] not in (None, False, "false")}
    return AXItem(
        role=node.get("role", {}).get("value", ""),
        name=(node.get("name", {}).get("value") or "").strip(),
        value=str(node.get("value", {}).get("value") or ""),
        level=props.get("level"),
        states=states,
        backend_id=node.get("backendDOMNodeId"),
        focusable=bool(props.get("focusable")),
        live=live or props.get("live") in ("polite", "assertive"),
        child_count=len(node.get("childIds", [])),
    )


def flatten(nodes: list[dict]) -> tuple[str, list[AXItem]]:
    """Turn CDP's node list into (page title, reading order), like browse mode."""
    by_id = {n["nodeId"]: n for n in nodes}
    root = next(n for n in nodes if not n.get("parentId"))
    title = (root.get("name", {}).get("value") or "").strip()
    out: list[AXItem] = []

    def visible_children(node):
        for cid in node.get("childIds", []):
            child = by_id.get(cid)
            if child is not None:
                yield child

    def count_items(node) -> int:
        # For "list with N items": count listitem descendants one level down,
        # looking through ignored/generic wrappers.
        n = 0
        for child in visible_children(node):
            role = child.get("role", {}).get("value")
            if child.get("ignored") or role in ("generic", "none"):
                n += count_items(child)
            elif role == "listitem":
                n += 1
        return n

    def walk(node, live: bool):
        role = node.get("role", {}).get("value", "")
        if node.get("ignored"):
            for child in visible_children(node):
                walk(child, live)
            return
        item = item_from_node(node, live)
        live = item.live or role in LIVE_ROLES
        item.live = live
        if role in LEAF_ROLES:
            if role != "StaticText" or item.name:
                out.append(item)
            return
        if role in ANNOUNCED_CONTAINERS:
            if role == "list":
                item.child_count = count_items(node)
            out.append(item)
        for child in visible_children(node):
            walk(child, live)

    for child in visible_children(root):
        walk(child, False)
    return title, out


class AXReader:
    """Fetches accessibility information for a Playwright page via CDP."""

    def __init__(self, page):
        self.page = page
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.send("Accessibility.enable")
        self.cdp.send("DOM.enable")

    def snapshot(self) -> tuple[str, list[AXItem]]:
        nodes = self.cdp.send("Accessibility.getFullAXTree")["nodes"]
        return flatten(nodes)

    def focused(self) -> AXItem | None:
        res = self.cdp.send("Runtime.evaluate", {"expression": "document.activeElement"})
        obj = res.get("result", {}).get("objectId")
        if not obj:
            return None
        backend = self.cdp.send("DOM.describeNode", {"objectId": obj})["node"]["backendNodeId"]
        nodes = self.cdp.send(
            "Accessibility.getPartialAXTree", {"backendNodeId": backend, "fetchRelatives": False}
        )["nodes"]
        if not nodes:
            return None
        item = item_from_node(nodes[0])
        item.backend_id = backend
        return item

    def focus(self, backend_id: int) -> None:
        self.cdp.send("DOM.focus", {"backendNodeId": backend_id})
