"""Fit a pointer profile to recorded mouse movements, and measure how close it is.

A recording is a set of aimed clicks (from examples/pointer_recorder.html):
for each trial, where the pointer started, the target square, every pointer
sample, and the button press and release. `measure` boils a set of trials
down to the numbers motor-control research uses to describe pointing:

- Fitts's law intercept and slope (movement time against difficulty),
- click scatter: a part that grows with target size and a fixed floor in
  pixels, and the miss rate (steered by how close to the aim point the user
  gets before clicking),
- how much paths bow, where in the movement the speed peaks, and how
  many corrective submovements there are,
- the first fast stroke: how far it reaches, how much that varies, and how
  long it takes,
- the pause on the target before pressing and how it grows on small
  targets, how long the button is held,
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


def _primary(tr: Trial, speeds: list[tuple[float, float]]) -> tuple[float, float] | None:
    """(share of the distance covered, share of the movement time taken) when
    the first fast stroke ends: the speed first falls below 25% of its peak."""
    if not speeds:
        return None
    peak_i = max(range(len(speeds)), key=lambda i: speeds[i][1])
    peak = speeds[peak_i][1]
    end_t = next((t for t, s in speeds[peak_i:] if s < 0.25 * peak), speeds[-1][0])
    t_on, mt = _onset(tr), tr.down[0] - _onset(tr)
    at = min(tr.moves, key=lambda m: abs(m[0] - end_t))
    left = math.dist(at[1:], tr.box.center)
    return 1 - left / tr.distance, (end_t - t_on) / mt


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
    # Scatter SD in pixels for each target size, then SD = floor + slope * W.
    by_width: dict[float, list[Trial]] = {}
    for t in trials:
        by_width.setdefault(t.width, []).append(t)
    widths = sorted(by_width)
    sds = [math.sqrt(statistics.mean(
        ((t.down[1] - t.box.center[0]) ** 2 + (t.down[2] - t.box.center[1]) ** 2) / 2 for t in by_width[w]))
        for w in widths]
    if len(widths) > 1:
        floor, slope = _fit_line(widths, sds)
    else:
        floor, slope = math.nan, sds[0] / widths[0]

    settle, drift, peak_at, subs, reach, primary_t, primary_s = [], [], [], [], [], [], []
    settle_by_width: dict[float, list[float]] = {}
    for t in trials:
        before = [m for m in t.moves if m[0] <= t.down[0]]
        if before:
            settle.append(t.down[0] - before[-1][0])
            settle_by_width.setdefault(t.width, []).append(settle[-1])
        held = [m for m in t.moves if t.down[0] < m[0] <= t.up[0]] + [t.up]
        drift.append(max(math.dist(m[1:], t.down[1:]) for m in held))
        sp = _speeds(t)
        mt = t.down[0] - _onset(t)
        if sp and mt > 0 and t.distance > 100:
            peak_t = max(sp, key=lambda p: p[1])[0]
            peak_at.append((peak_t - _onset(t)) / mt)
            subs.append(_submovements(sp))
            first = _primary(t, sp)
            if first:
                reach.append(first[0])
                primary_t.append(first[1])
                primary_s.append(first[1] * mt)
    far = [t for t in trials if t.distance > 100]
    # Pause before pressing against target size, on log scales:
    # log(settle) = log(settle at 24 px) - exp * log(W / 24).
    ws = sorted(w for w, v in settle_by_width.items() if statistics.median(v) > 0)
    if len(ws) > 1:
        c, m = _fit_line([math.log(w / 24) for w in ws], [math.log(statistics.median(settle_by_width[w])) for w in ws])
        settle_24, settle_exp = math.exp(c), -m
    else:
        settle_24, settle_exp = (statistics.median(settle) if settle else math.nan), math.nan
    return {
        "fitts_a_s": a,
        "fitts_b_s_per_bit": b,
        "miss_rate": 1 - len(hits) / len(trials),
        "click_scatter": slope * 4.133,
        "scatter_floor_px": floor,
        "path_bow": statistics.mean(_bow(t) for t in far) if far else math.nan,
        "first_stroke_reach": statistics.median(reach) if reach else math.nan,
        "first_stroke_reach_sd": statistics.stdev(reach) if len(reach) > 1 else math.nan,
        "first_stroke_s": statistics.median(primary_s) if primary_s else math.nan,
        "first_stroke_time": statistics.median(primary_t) if primary_t else math.nan,
        "settle_s": settle_24,
        "settle_size_exp": settle_exp,
        "hold_s": statistics.median(t.up[0] - t.down[0] for t in trials),
        "hold_drift_px": statistics.median(drift),  # median: a stray drag is not a tremor
        "peak_speed_at": statistics.median(peak_at) if peak_at else math.nan,
        "submovements": statistics.mean(subs) if subs else math.nan,
    }


# The measures fit_profile matches, and the profile field each one steers.
FITTED = {
    "fitts_a_s": "pointer_a_s",
    "fitts_b_s_per_bit": "pointer_b_s",
    "click_scatter": "pointer_spread",
    "scatter_floor_px": "pointer_noise_px",
    "path_bow": "pointer_curvature",
    "first_stroke_reach": "pointer_reach",
    "first_stroke_reach_sd": "pointer_reach_sd",
    "first_stroke_s": "pointer_flick_s",
    "settle_s": "pointer_settle_s",
    "settle_size_exp": "pointer_settle_exp",
    "hold_s": "pointer_hold_s",
    "hold_drift_px": "pointer_tremor_px",
    "miss_rate": "pointer_stop_tol",
}


def fit_profile(trials: list[Trial], base: Profile | str = "intermediate", name: str = "me",
                iterations: int = 16, repeats: int = 2,
                fields: set[str] | None = None) -> tuple[Profile, dict, dict]:
    """Tune `base`'s pointer fields so its simulated clicks on these same
    targets match the recording. Returns (profile, recorded, simulated).

    Each round simulates, compares each measure, and nudges the field that
    drives it (the intercept additively, the rest by ratio). The model's
    internals (submovements, clamping, rounding) sit between a field and its
    measure, so matching by simulation is more faithful than plugging the
    recorded numbers straight in. `fields` limits fitting to some pointer
    fields (the rest keep `base`'s values), e.g. to compare model variants.
    """
    fitted = {k: f for k, f in FITTED.items() if fields is None or f in fields}
    if isinstance(base, str):
        base = load_profile(base)
    target = measure(trials)
    p = replace(base, name=name, description=f"Pointer fitted to {len(trials)} recorded clicks",
                notes=[*base.notes, f"pointer fields fitted from a recording (base {base.name})"])
    if target["hold_drift_px"] > 0.5 and p.pointer_tremor_px == 0:
        p = replace(p, pointer_tremor_px=1.0)
    # Fitted profiles use a fixed-length first flick, which real recordings
    # show (its share of the movement time is then a check, not a setting).
    if "first_stroke_s" in fitted and not math.isnan(target["first_stroke_s"]):
        p = replace(p, pointer_flick_s=target["first_stroke_s"])
    def misfit(sim: dict) -> float:
        """Total squared relative error over the fitted measures."""
        total = 0.0
        for key in fitted:
            want, got = target[key], sim[key]
            if not (math.isnan(want) or math.isnan(got)):
                total += ((got - want) / max(abs(want), 0.02)) ** 2
        return total

    sim = measure(simulate(p, trials, repeats))
    best = (misfit(sim), p, sim)
    for _ in range(iterations):
        changes = {}
        for key, fld in fitted.items():
            want, got, cur = target[key], sim[key], getattr(p, fld)
            if math.isnan(want) or math.isnan(got):
                continue
            if key in ("fitts_a_s", "scatter_floor_px"):
                changes[fld] = max(0.0, cur + (want - got))
            elif key == "miss_rate":
                if want > 0 and got < 1e-6:
                    changes[fld] = cur * 1.5  # no misses yet: loosen and look again
                elif got > 1e-6:
                    changes[fld] = min(max(cur * min(max(want / got, 0.67), 1.5), 0.0), 2.0)
            elif key == "settle_size_exp":
                changes[fld] = min(max(cur + (want - got), 0.0), 2.0)
            elif key == "hold_drift_px" and want <= 0.5:
                changes[fld] = 0.0  # a steady hand: no tremor at all
            elif got > 1e-6:
                ratio = min(max(want / got, 0.5), 2.0)  # damped, so it cannot run away
                changes[fld] = max(cur * ratio, 1e-3)
        if "pointer_reach" in changes:
            changes["pointer_reach"] = min(changes["pointer_reach"], 1.1)
        p = replace(p, **changes)
        sim = measure(simulate(p, trials, repeats))
        if misfit(sim) < best[0]:
            best = (misfit(sim), p, sim)
    # Simulation noise makes later rounds wander a little: keep the best one.
    _, p, sim = best
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
