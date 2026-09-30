"""Fit a pointer profile to recorded mouse movements, and measure how close it is.

A recording is a set of aimed clicks (from examples/pointer_recorder.html):
for each trial, where the pointer started, the target square, every pointer
sample, and the button press and release. `measure` boils a set of trials
down to the numbers motor-control research uses to describe pointing:

- Fitts's law intercept and slope (movement time against difficulty),
- click scatter relative to target size, and the miss rate,
- how much paths bow, where in the movement the speed peaks, and how
  many corrective submovements there are,
- the pause on the target before pressing, how long the button is held,
  and how far the pointer drifts while it is held.

`fit_profile` then adjusts the pointer_* fields of a base profile until the
simulated user, clicking the *same* targets from the same start points,
produces the same numbers. `compare` prints recorded vs simulated side by
side, including the measures that are not fitted, which shows where the
model itself differs from a real hand.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, replace
from pathlib import Path

from .pointer import PointerEvent, PointerPlanner, Rect
from .profiles import Profile, load_profile
from .timing import Humanizer

Sample = tuple[float, float, float]  # (seconds from trial start, x, y)


@dataclass(frozen=True)
class Trial:
    start: tuple[float, float]
    box: Rect
    moves: tuple[Sample, ...]
    down: Sample
    up: Sample

    @property
    def distance(self) -> float:
        return math.dist(self.start, self.box.center)

    @property
    def width(self) -> float:
        return min(self.box.width, self.box.height)

    @property
    def difficulty(self) -> float:
        """Fitts's index of difficulty in bits (Shannon form)."""
        return math.log2(self.distance / self.width + 1)

    @property
    def hit(self) -> bool:
        return self.box.contains(*self.down[1:]) and self.box.contains(*self.up[1:])


# --- loading ---------------------------------------------------------------------


def _trial_from_json(t: dict) -> Trial:
    ms = lambda s: (s[0] / 1000, float(s[1]), float(s[2]))  # noqa: E731
    return Trial(start=tuple(t["start"]), box=Rect(*t["target"]),
                 moves=tuple(ms(m) for m in t["moves"]), down=ms(t["down"]), up=ms(t["up"]))


def load_recording(source) -> list[Trial]:
    """Trials from a recorder JSON file, its parsed contents, or a list of
    the documents the recorder saves (one per click, or one per block)."""
    if isinstance(source, (str, Path)):
        source = json.loads(Path(source).read_text(encoding="utf-8"))
    docs = source if isinstance(source, list) else source.get("blocks", [source])
    key = lambda d: (d.get("session", ""), d.get("block", 0), d.get("trial", 0))  # noqa: E731
    trials = []
    for doc in sorted(docs, key=key):
        trials += [_trial_from_json(t) for t in doc.get("trials", [doc])]
    if not trials:
        raise ValueError("the recording has no trials")
    return trials


def trial_from_events(start, box: Rect, events: list[PointerEvent]) -> Trial:
    """A simulated click, in the same shape as a recorded one."""
    t, moves, down, up = 0.0, [], None, None
    for e in events:
        t += e.delay_before
        if e.kind == "move":
            moves.append((t, e.x, e.y))
        elif e.kind == "down" and down is None:
            down = (t, e.x, e.y)
        elif e.kind == "up" and up is None:
            up = (t, e.x, e.y)
    return Trial(tuple(start), box, tuple(moves), down, up)


def simulate(profile: Profile, trials: list[Trial], repeats: int = 3, seed: int = 0) -> list[Trial]:
    """The profile clicking the same targets from the same start points.
    Fixed seeds, so two profiles are compared on identical random draws."""
    out = []
    for r in range(repeats):
        for i, tr in enumerate(trials):
            planner = PointerPlanner(Humanizer(profile.with_seed(seed + r * 100_000 + i)))
            out.append(trial_from_events(tr.start, tr.box, planner.plan_click(tr.start, tr.box)))
    return out


# --- measuring ---------------------------------------------------------------------


def _onset(tr: Trial) -> float:
    """When the hand started moving: the first sample 3 px from the start."""
    for t, x, y in tr.moves:
        if math.dist((x, y), tr.start) >= 3:
            return t
    return tr.down[0]


def _speeds(tr: Trial) -> list[tuple[float, float]]:
    """(time, speed in px/s) during the movement, lightly smoothed over
    ~25 ms windows so recordings at any sample rate compare fairly."""
    t_on = _onset(tr)
    pts = [(t, x, y) for t, x, y in tr.moves if t_on - 0.01 <= t <= tr.down[0]]
    out = []
    j = 0
    for i in range(len(pts)):
        while pts[i][0] - pts[j][0] > 0.025:
            j += 1
        if i > j:
            dt = pts[i][0] - pts[j][0]
            out.append(((pts[i][0] + pts[j][0]) / 2, math.dist(pts[i][1:], pts[j][1:]) / dt))
    return out


def _submovements(speeds: list[tuple[float, float]]) -> int:
    """Count speed bursts: the speed falls below 15% of its peak between them."""
    if not speeds:
        return 0
    peak = max(s for _, s in speeds)
    count, moving = 0, False
    for _, s in speeds:
        if not moving and s > 0.3 * peak:
            count, moving = count + 1, True
        elif moving and s < 0.15 * peak:
            moving = False
    return count


def _bow(tr: Trial) -> float:
    """Largest sideways distance from the straight start-to-target line, / D."""
    (sx, sy), (cx, cy) = tr.start, tr.box.center
    d = tr.distance
    if d < 1:
        return 0.0
    return max((abs((cx - sx) * (sy - y) - (sx - x) * (cy - sy)) / d for _, x, y in tr.moves
                if _onset(tr) <= _ <= tr.down[0]), default=0.0) / d


def _fit_line(xs, ys) -> tuple[float, float]:
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
    return my - b * mx, b


def measure(trials: list[Trial]) -> dict[str, float]:
    hits = [t for t in trials if t.hit]
    mts = [t.down[0] - _onset(t) for t in hits]
    a, b = _fit_line([t.difficulty for t in hits], mts) if len(hits) > 2 else (math.nan, math.nan)
    scatter = math.sqrt(statistics.mean(
        ((t.down[1] - t.box.center[0]) / t.width) ** 2 + ((t.down[2] - t.box.center[1]) / t.width) ** 2
        for t in trials) / 2) * 4.133

    settle, drift, peak_at, subs = [], [], [], []
    for t in trials:
        before = [m for m in t.moves if m[0] <= t.down[0]]
        if before:
            settle.append(t.down[0] - before[-1][0])
        held = [m for m in t.moves if t.down[0] < m[0] <= t.up[0]] + [t.up]
        drift.append(max(math.dist(m[1:], t.down[1:]) for m in held))
        sp = _speeds(t)
        mt = t.down[0] - _onset(t)
        if sp and mt > 0 and t.distance > 100:
            peak_t = max(sp, key=lambda p: p[1])[0]
            peak_at.append((peak_t - _onset(t)) / mt)
            subs.append(_submovements(sp))
    far = [t for t in trials if t.distance > 100]
    return {
        "fitts_a_s": a,
        "fitts_b_s_per_bit": b,
        "miss_rate": 1 - len(hits) / len(trials),
        "click_scatter": scatter,
        "path_bow": statistics.mean(_bow(t) for t in far) if far else math.nan,
        "settle_s": statistics.median(settle) if settle else math.nan,
        "hold_s": statistics.median(t.up[0] - t.down[0] for t in trials),
        "hold_drift_px": statistics.mean(drift),
        "peak_speed_at": statistics.median(peak_at) if peak_at else math.nan,
        "submovements": statistics.mean(subs) if subs else math.nan,
    }


# The measures fit_profile matches, and the profile field each one steers.
FITTED = {
    "fitts_a_s": "pointer_a_s",
    "fitts_b_s_per_bit": "pointer_b_s",
    "click_scatter": "pointer_spread",
    "path_bow": "pointer_curvature",
    "settle_s": "pointer_settle_s",
    "hold_s": "pointer_hold_s",
    "hold_drift_px": "pointer_tremor_px",
}


def fit_profile(trials: list[Trial], base: Profile | str = "intermediate", name: str = "me",
                iterations: int = 10, repeats: int = 2) -> tuple[Profile, dict, dict]:
    """Tune `base`'s pointer fields so its simulated clicks on these same
    targets match the recording. Returns (profile, recorded, simulated).

    Each round simulates, compares each measure, and nudges the field that
    drives it (the intercept additively, the rest by ratio). The model's
    internals (submovements, clamping, rounding) sit between a field and its
    measure, so matching by simulation is more faithful than plugging the
    recorded numbers straight in.
    """
    if isinstance(base, str):
        base = load_profile(base)
    target = measure(trials)
    p = replace(base, name=name, description=f"Pointer fitted to {len(trials)} recorded clicks",
                notes=[*base.notes, f"pointer fields fitted from a recording (base {base.name})"])
    if target["hold_drift_px"] > 0.5 and p.pointer_tremor_px == 0:
        p = replace(p, pointer_tremor_px=1.0)
    sim = measure(simulate(p, trials, repeats))
    for _ in range(iterations):
        changes = {}
        for key, fld in FITTED.items():
            want, got, cur = target[key], sim[key], getattr(p, fld)
            if math.isnan(want) or math.isnan(got):
                continue
            if key == "fitts_a_s":
                changes[fld] = max(0.0, cur + (want - got))
            elif key == "hold_drift_px" and want <= 0.5:
                changes[fld] = 0.0  # a steady hand: no tremor at all
            elif got > 1e-6:
                ratio = min(max(want / got, 0.5), 2.0)  # damped, so it cannot run away
                changes[fld] = max(cur * ratio, 1e-3)
        p = replace(p, **changes)
        sim = measure(simulate(p, trials, repeats))
    return p, target, sim


def compare(recorded: dict, simulated: dict) -> str:
    """A recorded-vs-simulated table. Fitted measures are marked with *."""
    lines = [f"{'measure':<22}{'you':>10}{'model':>10}   {'off by':>7}"]
    for key, want in recorded.items():
        got = simulated[key]
        off = "" if math.isnan(want) or math.isnan(got) or abs(want) < 1e-9 else f"{(got - want) / abs(want):+.0%}"
        mark = "*" if key in FITTED else " "
        lines.append(f"{mark}{key:<21}{want:>10.3f}{got:>10.3f}   {off:>7}")
    lines.append("* = fitted; the rest show how the model's shape compares with yours")
    return "\n".join(lines)
