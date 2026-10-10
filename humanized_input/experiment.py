"""Compare mouse styles on targets of any shape: which one lands its clicks?

A MouseStyle combines a Profile (all the pointer_* settings) with a few
switches that are easy to compare:

- path:  "human" (the full pointer model), "linear" (a straight line at
  constant speed), "ease" (pyautogui.moveTo with easeOutQuad over a random
  duration in ease_s, as simple bots do) or "instant" (jump straight there).
- aim:   where in the target to click. "human" scatters around the shape's
  centre like a person; "center" is the bounding-box centre; "middle" a
  point scattered around it and kept in the box's middle half; "centroid" the
  centre of the shape's area; "deepest" the point furthest from every edge;
  "uniform" a random point inside the shape.
- speed: multiplies the pace (2.0 = everything takes half as long).
- aim_jitter_px: extra random offset added to any aim.
- lead: for a moving target (a velocity given to plan()), aim where it will
  be during the press, not where it is now.
- clicks / button: double clicks, right clicks.

Targets are Shapes: a rectangle, or a polygon for irregular outlines (a
character model, an object in a scene). The bounding-box centre of an
L-shaped or hollow outline is not on the shape at all, which is the kind of
thing these comparisons show.

    styles = grid("intermediate", path=["human", "linear"], aim=["center", "deepest"], speed=[1, 2])
    print(table(evaluate(styles, [Shape.rect(600, 400, 24, 24), Shape.polygon(l_outline)])))

Plain Python, no dependencies beyond this package.
"""

from __future__ import annotations

import itertools
import math
import random
import statistics
from dataclasses import dataclass, field, replace

from .pointer import POLL_HZ, PointerEvent, PointerPlanner, Rect
from .profiles import Profile, load_profile
from .timing import Humanizer

PATHS = ("human", "linear", "ease", "instant")
AIMS = ("human", "center", "middle", "centroid", "deepest", "uniform")
BUTTONS = ("left", "right", "middle")

Point = tuple[float, float]


# --- shapes --------------------------------------------------------------------------


@dataclass(frozen=True)
class Shape:
    """A closed outline in screen pixels (even-odd rule, so holes work if the
    outline doubles back around them)."""

    points: tuple[Point, ...]

    @classmethod
    def rect(cls, x: float, y: float, w: float, h: float) -> "Shape":
        return cls(((x, y), (x + w, y), (x + w, y + h), (x, y + h)))

    @classmethod
    def polygon(cls, points) -> "Shape":
        pts = tuple((float(x), float(y)) for x, y in points)
        if len(pts) < 3:
            raise ValueError("a polygon needs at least 3 points")
        return cls(pts)

    @property
    def box(self) -> Rect:
        xs, ys = [p[0] for p in self.points], [p[1] for p in self.points]
        return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def moved(self, dx: float, dy: float) -> "Shape":
        return Shape(tuple((x + dx, y + dy) for x, y in self.points))

    def _edges(self):
        return zip(self.points, self.points[1:] + self.points[:1])

    def contains(self, x: float, y: float) -> bool:
        inside = False
        for (x1, y1), (x2, y2) in self._edges():
            if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        return inside

    def edge_distance(self, x: float, y: float) -> float:
        best = math.inf
        for (x1, y1), (x2, y2) in self._edges():
            dx, dy = x2 - x1, y2 - y1
            t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
            best = min(best, math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)))
        return best

    @property
    def centroid(self) -> Point:
        a = cx = cy = 0.0
        for (x1, y1), (x2, y2) in self._edges():
            cross = x1 * y2 - x2 * y1
            a += cross
            cx += (x1 + x2) * cross
            cy += (y1 + y2) * cross
        if abs(a) < 1e-9:
            return self.box.center
        return cx / (3 * a), cy / (3 * a)

    def deepest(self, step: float | None = None) -> Point:
        """The inside point furthest from every edge: the safest place to click."""
        b = self.box
        step = step or max(1.0, max(b.width, b.height) / 60)
        best, best_d = b.center, -1.0
        y = b.y + step / 2
        while y < b.y + b.height:
            x = b.x + step / 2
            while x < b.x + b.width:
                if self.contains(x, y):
                    d = self.edge_distance(x, y)
                    if d > best_d:
                        best, best_d = (x, y), d
                x += step
            y += step
        return best

    def random_point(self, rng: random.Random) -> Point:
        b = self.box
        for _ in range(10_000):
            p = (rng.uniform(b.x, b.x + b.width), rng.uniform(b.y, b.y + b.height))
            if self.contains(*p):
                return p
        return self.deepest()


# --- styles ------------------------------------------------------------------------------


@dataclass(frozen=True)
class MouseStyle:
    name: str
    profile: Profile = field(default_factory=lambda: load_profile("intermediate"))
    path: str = "human"
    aim: str = "human"
    speed: float = 1.0
    aim_jitter_px: float = 0.0
    lead: bool = False
    clicks: int = 1
    button: str = "left"
    ease_s: tuple[float, float] = (0.12, 0.3)
    description: str = ""

    def __post_init__(self):
        if self.path not in PATHS:
            raise ValueError(f"path must be one of {PATHS}, got {self.path!r}")
        if self.aim not in AIMS:
            raise ValueError(f"aim must be one of {AIMS}, got {self.aim!r}")
        if self.button not in BUTTONS:
            raise ValueError(f"button must be one of {BUTTONS}, got {self.button!r}")
        if self.speed <= 0 or self.aim_jitter_px < 0 or self.clicks < 1:
            raise ValueError("speed must be > 0, aim_jitter_px >= 0 and clicks >= 1")
        lo, hi = self.ease_s
        if not 0 <= lo <= hi:
            raise ValueError("ease_s must be (low, high) with 0 <= low <= high")

    @classmethod
    def from_dict(cls, data: dict) -> "MouseStyle":
        """{name, profile: <built-in name, file path, or an inline profile>,
        path, aim, speed, aim_jitter_px, lead, clicks, button, ease_s,
        overrides: {pointer_b_s: 0.2, ...}}"""
        data = dict(data)
        profile = data.pop("profile", "intermediate")
        profile = Profile.from_dict(profile) if isinstance(profile, dict) else load_profile(profile)
        overrides = data.pop("overrides", {}) or {}
        if "ease_s" in data:
            data["ease_s"] = tuple(data["ease_s"])
        return cls(profile=replace(profile, **overrides), **data)

    def to_dict(self) -> dict:
        """Everything needed to rebuild this style, with its profile inline."""
        return {"name": self.name, "description": self.description, "path": self.path, "aim": self.aim,
                "speed": self.speed, "aim_jitter_px": self.aim_jitter_px, "lead": self.lead,
                "clicks": self.clicks, "button": self.button, "ease_s": list(self.ease_s),
                "profile": self.profile.to_dict()}


def grid(base: Profile | str = "intermediate", path=("human",), aim=("human",), speed=(1.0,),
         aim_jitter_px=(0.0,), **profile_fields) -> list[MouseStyle]:
    """Every combination of the given values. Profile fields go in as lists
    too, e.g. grid(pointer_spread=[0.3, 0.6], pointer_flick_s=[0, 0.17])."""
    base = load_profile(base) if isinstance(base, str) else base
    names, values = zip(*profile_fields.items()) if profile_fields else ((), ())
    styles = []
    for p, a, s, j, *fv in itertools.product(path, aim, speed, aim_jitter_px, *values):
        overrides = dict(zip(names, fv))
        label = [f"{p}", f"aim={a}", f"x{s:g}"] + ([f"jitter={j:g}"] if j else [])
        label += [f"{k.removeprefix('pointer_')}={v:g}" for k, v in overrides.items()]
        styles.append(MouseStyle(" ".join(label), replace(base, **overrides), p, a, s, j))
    return styles


# --- planning one click ---------------------------------------------------------------------


def _aim_point(style: MouseStyle, shape: Shape, planner: PointerPlanner, rng: random.Random) -> Point:
    if style.aim == "human":
        # A person aims at the visual middle of the shape, with their own scatter.
        b = shape.box
        cx, cy = shape.centroid
        x, y = planner.aim_point(Rect(cx - b.width / 2, cy - b.height / 2, b.width, b.height))
    elif style.aim == "center":
        x, y = shape.box.center
    elif style.aim == "middle":
        # A typical simple bot: scatter of w/8 around the box centre, kept in its middle half.
        b = shape.box
        cx, cy = b.center
        x = min(max(rng.gauss(cx, b.width / 8), b.x + b.width * 0.25), b.x + b.width * 0.75)
        y = min(max(rng.gauss(cy, b.height / 8), b.y + b.height * 0.25), b.y + b.height * 0.75)
    elif style.aim == "centroid":
        x, y = shape.centroid
    elif style.aim == "deepest":
        x, y = shape.deepest()
    else:
        x, y = shape.random_point(rng)
    if style.aim_jitter_px:
        x, y = rng.gauss(x, style.aim_jitter_px), rng.gauss(y, style.aim_jitter_px)
    return x, y


def _ease_moves(start: Point, aim: Point, duration: float) -> list[PointerEvent]:
    """pyautogui.moveTo(..., duration, tween=easeOutQuad): it steps at most
    every 50 ms (pyautogui.MINIMUM_SLEEP), so short moves are a few big jumps."""
    dx, dy = aim[0] - start[0], aim[1] - start[1]
    steps = max(1, int(max(abs(dx), abs(dy))))
    if duration / steps < 0.05:
        steps = max(1, int(duration / 0.05))
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        f = -t * (t - 2)  # easeOutQuad
        out.append(PointerEvent("move", round(start[0] + dx * f), round(start[1] + dy * f), duration / steps))
    return out


def _plan_once(style: MouseStyle, start: Point, shape: Shape, seed: int) -> tuple[list[PointerEvent], Point]:
    planner = PointerPlanner(Humanizer(style.profile.with_seed(seed)))
    rng = random.Random(seed ^ 0x5EED)
    aim = _aim_point(style, shape, planner, rng)
    box = shape.box
    p = style.profile
    button = style.button

    if style.path == "human":
        events = planner.plan_click(start, box, button, clicks=style.clicks, aim=aim)
    else:
        target = (round(aim[0]), round(aim[1]))
        events = []
        if style.path == "linear":
            dist = math.dist(start, aim)
            duration = planner.fitts_time(dist, max(min(box.width, box.height), 1.0))
            steps = max(1, round(duration * POLL_HZ))
            last = (round(start[0]), round(start[1]))
            for i in range(1, steps + 1):
                f = i / steps
                xy = (round(start[0] + (aim[0] - start[0]) * f), round(start[1] + (aim[1] - start[1]) * f))
                if xy != last:
                    events.append(PointerEvent("move", *xy, 1 / POLL_HZ))
                    last = xy
        elif style.path == "ease":
            events = _ease_moves(start, aim, rng.uniform(*style.ease_s))
        if not events or (events[-1].x, events[-1].y) != target:
            events.append(PointerEvent("move", *target, 1 / POLL_HZ))
        pause = {"linear": p.pointer_settle_s, "ease": rng.uniform(0.03, 0.08)}.get(style.path, 0.02)
        hold = 0.0 if style.path == "ease" else p.pointer_hold_s  # pyautogui.click() has no hold
        for c in range(style.clicks):
            events.append(PointerEvent("down", *target, pause if c == 0 else 0.1, button))
            events.append(PointerEvent("up", *target, hold, button))

    if style.speed != 1.0:
        events = [replace(e, delay_before=e.delay_before / style.speed) for e in events]
    return events, aim


def _press_time(events: list[PointerEvent]) -> float:
    """Halfway between the first press and its release: the moment to centre
    a moving target on, so both land on it."""
    t, down = 0.0, None
    for e in events:
        t += e.delay_before
        if e.kind == "down" and down is None:
            down = t
        elif e.kind == "up" and down is not None:
            return (down + t) / 2
    return t


def plan(style: MouseStyle, start: Point, shape: Shape, seed: int = 0, button: str | None = None,
         velocity: Point | None = None) -> tuple[list[PointerEvent], Point]:
    """The events for one click on `shape` from `start`, and the aim point.

    `velocity` (px/s) is how fast the target is moving. With style.lead the
    aim goes where the target will be mid-press (between the button going
    down and coming up, so both land on it); without it
    the click is planned on where the target is now.
    """
    if button is not None and button != style.button:
        style = replace(style, button=button)
    events, aim = _plan_once(style, start, shape, seed)
    if style.lead and velocity and any(velocity):
        for _ in range(3):  # the move's length depends on the aim, so refine a few times
            t = _press_time(events)
            events, aim = _plan_once(style, start, shape.moved(velocity[0] * t, velocity[1] * t), seed)
    return events, aim


def landed(events: list[PointerEvent], shape: Shape, velocity: Point | None = None) -> bool:
    """True if every press and release happened on the shape (on where a
    moving shape had got to at that moment)."""
    t, presses = 0.0, []
    for e in events:
        t += e.delay_before
        if e.kind in ("down", "up"):
            presses.append((e, t))
    if not presses:
        return False
    vx, vy = velocity or (0.0, 0.0)
    return all(shape.contains(e.x - vx * t, e.y - vy * t) for e, t in presses)


# --- comparing ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Result:
    style: str
    clicks: int
    hit_rate: float
    mean_s: float
    p90_s: float


def evaluate(styles: list[MouseStyle], shapes: list, trials: int = 200, seed: int = 0,
             start_distance: tuple[float, float] = (150, 500)) -> list[Result]:
    """Click each shape `trials` times per style from random start points
    `start_distance` px away. Every style sees the same start points. A
    target can be a Shape, or (Shape, (vx, vy)) for one moving in px/s."""
    rng = random.Random(seed)
    jobs = []
    for i in range(trials):
        target = shapes[i % len(shapes)]
        shape, velocity = target if isinstance(target, tuple) else (target, None)
        cx, cy = shape.box.center
        a, d = rng.uniform(0, 2 * math.pi), rng.uniform(*start_distance)
        jobs.append((shape, velocity, (cx + d * math.cos(a), cy + d * math.sin(a)), seed * 1_000_003 + i))
    results = []
    for style in styles:
        hits, times = 0, []
        for shape, velocity, start, s in jobs:
            events, _ = plan(style, start, shape, s, velocity=velocity)
            hits += landed(events, shape, velocity)
            times.append(sum(e.delay_before for e in events))
        q = statistics.quantiles(times, n=10)[-1] if len(times) > 1 else times[0]
        results.append(Result(style.name, len(jobs), hits / len(jobs), statistics.mean(times), q))
    return results


def table(results: list[Result], sort: str = "hit_rate") -> str:
    rows = sorted(results, key=lambda r: (-r.hit_rate, r.mean_s) if sort == "hit_rate" else (r.mean_s, -r.hit_rate))
    width = max(len(r.style) for r in rows)
    lines = [f"{'style':<{width}}  {'hits':>6}  {'mean s':>7}  {'p90 s':>6}"]
    lines += [f"{r.style:<{width}}  {r.hit_rate:>6.1%}  {r.mean_s:>7.3f}  {r.p90_s:>6.3f}" for r in rows]
    return "\n".join(lines)


def execute(events: list[PointerEvent], move, down, up, sleep=None) -> None:
    """Play events through your own functions: move(x, y), down(button),
    up(button). `sleep` defaults to time.sleep."""
    import time

    sleep = sleep or time.sleep
    for e in events:
        if e.delay_before > 0:
            sleep(e.delay_before)
        if e.kind == "move":
            move(e.x, e.y)
        elif e.kind == "down":
            down(e.button)
        else:
            up(e.button)
