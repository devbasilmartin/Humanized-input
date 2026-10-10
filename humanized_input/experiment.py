"""Compare mouse styles on targets of any shape: which one lands its clicks?

A MouseStyle combines a Profile (all the pointer_* settings) with a few
switches that are easy to compare:

- path:  "human" (the full pointer model), "linear" (a straight line at
  constant speed) or "instant" (jump straight there).
- aim:   where in the target to click. "human" scatters around the shape's
  centre like a person; "center" is the bounding-box centre; "centroid" the
  centre of the shape's area; "deepest" the point furthest from every edge;
  "uniform" a random point inside the shape.
- speed: multiplies the pace (2.0 = everything takes half as long).
- aim_jitter_px: extra random offset added to any aim.

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

PATHS = ("human", "linear", "instant")
AIMS = ("human", "center", "centroid", "deepest", "uniform")

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

    def __post_init__(self):
        if self.path not in PATHS:
            raise ValueError(f"path must be one of {PATHS}, got {self.path!r}")
        if self.aim not in AIMS:
            raise ValueError(f"aim must be one of {AIMS}, got {self.aim!r}")
        if self.speed <= 0 or self.aim_jitter_px < 0:
            raise ValueError("speed must be > 0 and aim_jitter_px >= 0")

    @classmethod
    def from_dict(cls, data: dict) -> "MouseStyle":
        """{name, profile: <built-in name or file>, path, aim, speed,
        aim_jitter_px, overrides: {pointer_b_s: 0.2, ...}}"""
        data = dict(data)
        profile = load_profile(data.pop("profile", "intermediate"))
        overrides = data.pop("overrides", {}) or {}
        return cls(profile=replace(profile, **overrides), **data)


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
    elif style.aim == "centroid":
        x, y = shape.centroid
    elif style.aim == "deepest":
        x, y = shape.deepest()
    else:
        x, y = shape.random_point(rng)
    if style.aim_jitter_px:
        x, y = rng.gauss(x, style.aim_jitter_px), rng.gauss(y, style.aim_jitter_px)
    return x, y


def plan(style: MouseStyle, start: Point, shape: Shape, seed: int = 0,
         button: str = "left") -> tuple[list[PointerEvent], Point]:
    """The events for one click on `shape` from `start`, and the aim point."""
    planner = PointerPlanner(Humanizer(style.profile.with_seed(seed)))
    rng = random.Random(seed ^ 0x5EED)
    aim = _aim_point(style, shape, planner, rng)
    box = shape.box
    p = style.profile

    if style.path == "human":
        events = planner.plan_click(start, box, button, aim=aim)
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
        if not events or (events[-1].x, events[-1].y) != target:
            events.append(PointerEvent("move", *target, 1 / POLL_HZ))
        events.append(PointerEvent("down", *target, p.pointer_settle_s if style.path == "linear" else 0.02, button))
        events.append(PointerEvent("up", *target, p.pointer_hold_s, button))

    if style.speed != 1.0:
        events = [replace(e, delay_before=e.delay_before / style.speed) for e in events]
    return events, aim


def landed(events: list[PointerEvent], shape: Shape) -> bool:
    """True if every press and release happened on the shape."""
    presses = [e for e in events if e.kind in ("down", "up")]
    return bool(presses) and all(shape.contains(e.x, e.y) for e in presses)


# --- comparing ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Result:
    style: str
    clicks: int
    hit_rate: float
    mean_s: float
    p90_s: float


def evaluate(styles: list[MouseStyle], shapes: list[Shape], trials: int = 200, seed: int = 0,
             start_distance: tuple[float, float] = (150, 500)) -> list[Result]:
    """Click each shape `trials` times per style from random start points
    `start_distance` px away. Every style sees the same start points."""
    rng = random.Random(seed)
    jobs = []
    for i in range(trials):
        shape = shapes[i % len(shapes)]
        cx, cy = shape.box.center
        a, d = rng.uniform(0, 2 * math.pi), rng.uniform(*start_distance)
        jobs.append((shape, (cx + d * math.cos(a), cy + d * math.sin(a)), seed * 1_000_003 + i))
    results = []
    for style in styles:
        hits, times = 0, []
        for shape, start, s in jobs:
            events, _ = plan(style, start, shape, s)
            hits += landed(events, shape)
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
