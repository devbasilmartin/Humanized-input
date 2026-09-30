"""Quick checks for problems a screen reader user would run into.

This is not a replacement for axe-core or a manual audit; it looks only at
what our virtual screen reader can see, which makes the link between a bug
and a user's experience easy to follow.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ax import LANDMARK_ROLES, AXItem
from .screen_reader import describe


@dataclass
class Issue:
    rule: str
    message: str
    heard_as: str = ""


def audit(title: str, items: list[AXItem]) -> list[Issue]:
    issues: list[Issue] = []
    if not title:
        issues.append(Issue("page-title", "Page has no title; users hear 'untitled document'."))

    headings = [i for i in items if i.role == "heading"]
    if not any(h.level == 1 for h in headings):
        issues.append(Issue("h1", "No level 1 heading; users pressing 1 or H find no page heading."))
    last = 0
    for h in headings:
        if h.level and last and h.level > last + 1:
            issues.append(Issue("heading-skip", f"Heading jumps from level {last} to {h.level}.", describe(h)))
        last = h.level or last

    if not any(i.role == "main" for i in items):
        issues.append(Issue("main-landmark", "No main landmark; D / landmark navigation cannot skip to content."))
    if not any(i.role in LANDMARK_ROLES for i in items):
        issues.append(Issue("landmarks", "No landmarks at all."))

    for i in items:
        if i.is_interactive and not i.name:
            issues.append(Issue("unnamed-control", f"A {i.role} has no accessible name.", describe(i)))
        if i.role in ("image", "img") and not i.name:
            issues.append(Issue("image-alt", "Image has no alt text.", describe(i)))
        if i.role == "link" and i.name.casefold() in ("click here", "here", "more", "read more"):
            issues.append(Issue("vague-link", f"Link text {i.name!r} means nothing out of context.", describe(i)))
    return issues
