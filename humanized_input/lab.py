"""Read a Pointer Lab recording (examples/pointer_lab.html) and update a style to match.

    python -m humanized_input.lab pointer-lab.json                       # print the report
    python -m humanized_input.lab pointer-lab.json --style me.yaml --out me.yaml

The ring recorder fits timing and scatter. The lab covers what it can't:

- shapes: where in an odd shape you click (the box middle, the centroid, or
  the deepest point), which sets `aim`;
- moving: whether you aim where a moving target is, or where it will be,
  which sets `lead`;
- reaction: how long before you start moving when a target appears, which
  sets `reaction_s`;
- hold still: how much the pointer drifts while you try to keep it still,
  which sets `pointer_tremor_px`.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

from .experiment import MouseStyle, Shape

AIMS = ("middle", "centroid", "deepest")


def load_lab(source) -> dict[str, list[dict]]:
    """Trials by task, from a lab JSON file, its parsed contents, or a list of the saved documents."""
    if isinstance(source, (str, Path)):
        source = json.loads(Path(source).read_text(encoding="utf-8"))
    docs = source if isinstance(source, list) else source.get("trials", [])
    out: dict[str, list[dict]] = {}
    for d in sorted(docs, key=lambda d: (d.get("session", ""), d.get("task", ""), d.get("trial", 0))):
        out.setdefault(d["task"], []).append(d)
    if not out:
        raise ValueError("the recording has no trials")
    return out


def _onset(d: dict) -> float | None:
    """Seconds until the pointer first moved 3 px from where it was."""
    for t, x, y in d["moves"]:
        if math.dist((x, y), d["start"]) >= 3:
            return t / 1000
    return None


def shapes(trials: list[dict]) -> dict:
    """Which aim rule lands closest to where you clicked, and by how much."""
    dist = {a: [] for a in AIMS}
    for d in trials:
        if not d.get("down"):
            continue
        shape = Shape.polygon(d["polygon"])
        press = d["down"][1:]
        targets = {"middle": shape.box.center, "centroid": shape.centroid, "deepest": shape.deepest()}
        for a in AIMS:
            dist[a].append(math.dist(press, targets[a]))
    if not dist["middle"]:
        return {}
    med = {a: statistics.median(v) for a, v in dist.items()}
    best = min(med, key=med.get)
    return {"trials": len(dist["middle"]), "hit_rate": sum(d.get("hit", False) for d in trials) / len(trials),
            "median_px": {a: round(v, 1) for a, v in med.items()}, "aim": best}


def moving(trials: list[dict]) -> dict:
    """How far behind (or ahead of) a moving target's centre you press, as a share of the
    distance it travelled since the trial started. 1 means you aimed where it was at the
    start, 0 where it was at the press (leading)."""
    lags, hits, n, escaped = [], 0, 0, 0
    for d in trials:
        if d.get("escaped") or not d.get("down"):
            escaped += bool(d.get("escaped"))
            continue
        n += 1
        hits += bool(d.get("hit"))
        t = d["down"][0] / 1000
        vx = d["velocity"][0]
        x, _, w, _ = d["target"]
        centre = x + vx * t + w / 2
        travelled = abs(vx) * t
        if travelled > 5:
            lags.append((centre - d["down"][1]) * math.copysign(1, vx) / travelled)
    if not n:
        return {}
    lag = statistics.median(lags) if lags else math.nan
    return {"trials": n, "escaped": escaped, "hit_rate": hits / n, "lag": round(lag, 2), "lead": bool(lag < 0.5)}


def reaction(trials: list[dict]) -> dict:
    onsets = [o for d in trials if (o := _onset(d)) is not None and 0.08 < o < 2.0]
    if not onsets:
        return {}
    return {"trials": len(onsets), "reaction_s": round(statistics.median(onsets), 3),
            "spread_s": round(statistics.pstdev(onsets), 3)}


def hold(trials: list[dict]) -> dict:
    """Jitter while holding still: the pointer's distance from its own 200 ms running
    average. The model's tremor is a sine of amplitude A per axis, whose RMS is A / sqrt 2."""
    devs, samples, still = [], 0, 0
    for d in trials:
        pts = [(t / 1000, x, y) for t, x, y in d["moves"] if t >= 500]
        samples += len(pts)
        still += not pts
        for i, (t, x, y) in enumerate(pts):
            win = [p for p in pts if abs(p[0] - t) <= 0.1]
            mx, my = statistics.mean(p[1] for p in win), statistics.mean(p[2] for p in win)
            devs.append(((x - mx) ** 2 + (y - my) ** 2) / 2)
    rms = math.sqrt(statistics.mean(devs)) if devs else 0.0
    return {"trials": len(trials), "fully_still": still, "samples": samples,
            "rms_px": round(rms, 2), "tremor_px": round(rms * math.sqrt(2), 2)}


def analyze(source) -> dict:
    lab = load_lab(source)
    report = {}
    for task, fn in (("shapes", shapes), ("moving", moving), ("reaction", reaction), ("hold", hold)):
        if lab.get(task):
            report[task] = fn(lab[task])
    return report


def changes(report: dict) -> dict:
    """Style and profile settings the report suggests."""
    out = {}
    if report.get("shapes"):
        out["aim"] = report["shapes"]["aim"]
    if report.get("moving") and not math.isnan(report["moving"]["lag"]):
        out["lead"] = report["moving"]["lead"]
    if report.get("reaction"):
        out["reaction_s"] = report["reaction"]["reaction_s"]
    if report.get("hold"):
        out["pointer_tremor_px"] = report["hold"]["tremor_px"]
    return out


def describe(report: dict) -> str:
    lines = []
    if s := report.get("shapes"):
        m = s["median_px"]
        lines.append(f"Shapes ({s['trials']} clicks, {s['hit_rate']:.0%} hit): your clicks were a median "
                     f"{m['middle']} px from the box middle, {m['centroid']} px from the centroid and "
                     f"{m['deepest']} px from the deepest point, so aim={s['aim']}.")
    if m := report.get("moving"):
        how = "where it would be (leading)" if m["lead"] else "near where it was when you started"
        lines.append(f"Moving ({m['trials']} clicks, {m['hit_rate']:.0%} hit, {m['escaped']} got away): lag "
                     f"{m['lag']} (0 = leading, 1 = aiming at its start), so you aim {how}: lead={m['lead']}.")
    if r := report.get("reaction"):
        lines.append(f"Reaction ({r['trials']} trials): you start moving {r['reaction_s'] * 1000:.0f} ms after the "
                     f"target appears (SD {r['spread_s'] * 1000:.0f} ms).")
    if h := report.get("hold"):
        lines.append(f"Hold still ({h['trials']} trials, {h['samples']} samples, {h['fully_still']} with no movement "
                     f"at all): jitter {h['rms_px']} px RMS, so pointer_tremor_px={h['tremor_px']}.")
    return "\n".join(lines)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Analyse a Pointer Lab recording and update a style to match.")
    ap.add_argument("recording", help="the lab JSON file")
    ap.add_argument("--style", help="a style YAML file to update (for example your fitted me.yaml)")
    ap.add_argument("--out", help="where to write the updated style (default: print it)")
    args = ap.parse_args(argv)
    report = analyze(args.recording)
    print(describe(report))
    if args.style:
        import yaml

        from .styles import apply_changes

        style = MouseStyle.from_dict(yaml.safe_load(Path(args.style).read_text(encoding="utf-8")))
        text = yaml.safe_dump(apply_changes(style, changes(report)).to_dict(), sort_keys=False)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"saved {args.out}")
        else:
            print(text)


if __name__ == "__main__":
    main()
