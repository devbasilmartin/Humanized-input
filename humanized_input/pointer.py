"""Human-like mouse movement: where the pointer goes, how fast, and how it misses.

Nothing here touches a browser or the OS. PointerPlanner turns a Profile into
a list of PointerEvents (moves, button presses, delays), the same way
Humanizer.plan_typing turns text into keystrokes, so it is easy to test and study.

The model follows the motor-control literature:

1. **Fitts's law** sets how long an aimed movement takes:
   MT = a + b * log2(D / W + 1), where D is the distance and W the target
   width. Small, far targets take longer. `a` and `b` come from the profile.
2. **Submovements.** A person does not land a fast move exactly. The first,
   "ballistic" move tends to undershoot slightly and scatter around the aim
   point. If it ends outside the target, a short corrective move follows
   after a brief pause (Meyer et al.'s optimised-submovement model).
3. **Minimum-jerk velocity.** Each submovement starts slowly, peaks near the
   middle and slows to a stop: s(t) = 10t^3 - 15t^4 + 6t^5 (Flash & Hogan).
4. **Curved paths.** Hands pivot at the wrist and elbow, so paths bow
   slightly to one side instead of running in a straight line.
5. **Tremor.** For motor-impaired profiles, a small 4-8 Hz oscillation is
   added, also while the button is held down. On a small target that can
   drag the release off the target, so the click never happens. That is a
   real accessibility bug (WCAG 2.5.8 Target Size) found the way users hit it.

Aim points scatter around the target centre with a standard deviation of
W / 4.133. That is the "effective width" convention from Fitts's law research,
where about 96% of clicks land inside a target of width W.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .timing import Humanizer

POLL_HZ = 125  # a typical USB mouse reports 125 times a second
MAX_CORRECTIONS = 4


@dataclass(frozen=True)
class Rect:
    """A screen rectangle in pixels (CSS pixels in the browser)."""

    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    def inset(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.width - 2 * dx, self.height - 2 * dy)

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.width and self.y <= py < self.y + self.height


@dataclass(frozen=True)
class PointerEvent:
    kind: str  # "move", "down" or "up"
    x: int
    y: int
    delay_before: float  # seconds to wait before this event
    button: str = "left"


def min_jerk(t: float) -> float:
    """Fraction of the distance covered at normalised time t in [0, 1]."""
    return t**3 * (10 - 15 * t + 6 * t * t)


class PointerPlanner:
    def __init__(self, human: Humanizer):
        self.human = human
        self.profile = human.profile
        self.rng = human.rng

    # --- timing -------------------------------------------------------------

    def fitts_time(self, distance: float, width: float) -> float:
        """Seconds for an aimed movement (Shannon form of Fitts's law)."""
        p = self.profile
        width = max(width, 1.0)
        return p.pointer_a_s + p.pointer_b_s * math.log2(distance / width + 1)

    # --- where the hand aims and lands ---------------------------------------

    def aim_point(self, box: Rect) -> tuple[float, float]:
        """Where the user means to click: near the centre, scattered like
        real clicks, and always inside the box."""
        cx, cy = box.center
        sx = box.width / 4.133 * self.profile.pointer_spread
        sy = box.height / 4.133 * self.profile.pointer_spread
        safe = self._safe_zone(box)
        x = min(max(self.rng.gauss(cx, sx), safe.x), safe.x + safe.width)
        y = min(max(self.rng.gauss(cy, sy), safe.y), safe.y + safe.height)
        return x, y

    def _safe_zone(self, box: Rect) -> Rect:
        """The part of `box` the user is happy to stop in. Someone with a
        tremor keeps away from the edges, if the target leaves room."""
        margin = self.profile.pointer_tremor_px + 1
        return box.inset(min(margin, box.width / 2 - 0.5), min(margin, box.height / 2 - 0.5))

    def _landing(self, start, aim) -> tuple[float, float]:
        """Where a ballistic move toward `aim` actually ends: a little short on
        average, scattered more along the direction of travel than across it."""
        dx, dy = aim[0] - start[0], aim[1] - start[1]
        dist = math.hypot(dx, dy)
        if dist < 1:
            return aim
        err = self.profile.pointer_spread
        along = self.rng.gauss(0.95, 0.05 * err)  # humans tend to undershoot
        across = self.rng.gauss(0.0, 0.02 * err)
        ux, uy = dx / dist, dy / dist
        return (start[0] + dist * (along * ux - across * uy),
                start[1] + dist * (along * uy + across * ux))

    # --- the path itself ----------------------------------------------------

    def _tremor(self, t: float) -> tuple[float, float]:
        amp = self._tremor_amp
        if amp <= 0:
            return 0.0, 0.0
        return (amp * math.sin(2 * math.pi * self._tremor_hz * t + self._phase[0]),
                amp * math.sin(2 * math.pi * self._tremor_hz * 1.13 * t + self._phase[1]))

    def _submovement(self, start, end, duration: float, t0: float) -> list[tuple[float, float, float]]:
        """Points (x, y, time) along one bowed, minimum-jerk stroke."""
        dx, dy = end[0] - start[0], end[1] - start[1]
        dist = math.hypot(dx, dy)
        # Control point for a quadratic Bezier, pushed sideways off the midpoint.
        bow = self.rng.gauss(self.profile.pointer_curvature, self.profile.pointer_curvature / 2)
        bow *= self._handedness
        mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
        cx, cy = (mx - dy * bow, my + dx * bow) if dist else (mx, my)

        steps = max(2, round(duration * POLL_HZ))
        out = []
        for i in range(1, steps + 1):
            u = min_jerk(i / steps)
            x = (1 - u) ** 2 * start[0] + 2 * (1 - u) * u * cx + u * u * end[0]
            y = (1 - u) ** 2 * start[1] + 2 * (1 - u) * u * cy + u * u * end[1]
            out.append((x, y, t0 + i / POLL_HZ))
        return out

    def plan_move(self, start: tuple[float, float], box: Rect) -> tuple[list[PointerEvent], tuple[float, float]]:
        """Move from `start` into `box`. Returns (events, the resting position
        without tremor), so a click can follow from where the hand really is."""
        self._handedness = self.rng.choice((-1, 1))
        self._tremor_hz = self.rng.uniform(4.0, 8.0)
        # Tremor waxes and wanes (with effort, fatigue, stress), so its size
        # varies from one movement to the next around the profile's value.
        amp = self.profile.pointer_tremor_px
        self._tremor_amp = self.human._lognormal(amp, 0.4) if amp > 0 else 0.0
        self._phase = (self.rng.uniform(0, 2 * math.pi), self.rng.uniform(0, 2 * math.pi))
        self._t = 0.0

        aim = self.aim_point(box)
        safe = self._safe_zone(box)
        width = min(box.width, box.height)
        pos = (float(start[0]), float(start[1]))
        points: list[tuple[float, float, float]] = []
        for n in range(MAX_CORRECTIONS + 1):
            dist = math.hypot(aim[0] - pos[0], aim[1] - pos[1])
            if n and safe.contains(round(pos[0]), round(pos[1])):
                break  # the pointer reports whole pixels, so judge those
            last = n == MAX_CORRECTIONS
            end = aim if last or dist < 2 else self._landing(pos, aim)
            # The first stroke is most of the Fitts time; corrections are short.
            duration = self.fitts_time(dist, width) * (0.8 if n == 0 else 0.6)
            if n:
                self._t += self.human._lognormal(0.12, 0.3)  # see the miss, correct
            stroke = self._submovement(pos, end, duration, self._t)
            points += stroke
            self._t = stroke[-1][2]
            pos = end
        return self._to_events(points, start), pos

    def _to_events(self, points, start) -> list[PointerEvent]:
        events: list[PointerEvent] = []
        last_xy = (round(start[0]), round(start[1]))
        last_t = 0.0
        for x, y, t in points:
            tx, ty = self._tremor(t)
            xy = (round(x + tx), round(y + ty))
            if xy == last_xy:
                continue  # a mouse only reports when it has moved a whole pixel
            events.append(PointerEvent("move", xy[0], xy[1], t - last_t))
            last_xy, last_t = xy, t
        return events

    # --- clicking -------------------------------------------------------------

    def plan_click(self, start: tuple[float, float], box: Rect, button: str = "left",
                   clicks: int = 1) -> list[PointerEvent]:
        """Move into `box`, settle, press and release (twice for a double click)."""
        events, rest = self.plan_move(start, box)
        pos = (events[-1].x, events[-1].y) if events else (round(start[0]), round(start[1]))
        if self.profile.pointer_tremor_px <= 0:
            pos = (round(rest[0]), round(rest[1]))
            if not events or (events[-1].x, events[-1].y) != pos:
                events.append(PointerEvent("move", *pos, 1 / POLL_HZ))
        settle = self.human._lognormal(0.12, 0.3)  # verify the pointer is on target
        for c in range(clicks):
            gap = settle if c == 0 else self.human._lognormal(0.13, 0.25)  # double-click gap
            self._t += gap
            events.append(PointerEvent("down", *pos, gap, button))
            hold = self.human.key_hold()
            pos, drift = self._hold_drift(rest, pos, hold)
            events += drift
            events.append(PointerEvent("up", *pos, hold - sum(e.delay_before for e in drift), button))
        return events

    def _hold_drift(self, rest, pos, hold: float):
        """Tremor keeps moving the pointer while the button is held."""
        events = []
        steps = max(1, int(hold * POLL_HZ))
        for i in range(1, steps + 1):
            self._t += 1 / POLL_HZ
            tx, ty = self._tremor(self._t)
            xy = (round(rest[0] + tx), round(rest[1] + ty))
            if xy != pos:
                events.append(PointerEvent("move", xy[0], xy[1], 1 / POLL_HZ))
                pos = xy
        return pos, events


def pointer_path(events: list[PointerEvent]) -> list[tuple[int, int]]:
    """The positions the pointer visits, for checks and plots."""
    return [(e.x, e.y) for e in events]


def click_landed(events: list[PointerEvent], box: Rect) -> bool:
    """True if every press and release happened inside `box`, which is
    what a browser or desktop app needs to count it as a click."""
    presses = [e for e in events if e.kind in ("down", "up")]
    return bool(presses) and all(box.contains(e.x, e.y) for e in presses)
