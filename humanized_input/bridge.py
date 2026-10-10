"""Use mouse styles from any program, and record which ones work.

    python -m humanized_input.bridge styles.yaml --log outcomes.jsonl

Your program writes one JSON object per line to stdin and reads one reply
per line from stdout. It sends the target, plays back the events it gets
(move the mouse, wait `delay` seconds before each event), then reports
whether the click did what it should. The bridge picks a style for each
click (round-robin by default) so styles are compared on the same mix of
targets, and `summary` tallies the real results.

  -> {"cmd": "click", "from": [x, y], "rect": [x, y, w, h]}
  -> {"cmd": "click", "from": [x, y], "polygon": [[x, y], ...], "style": "optional name"}
  <- {"id": 1, "style": "...", "aim": [x, y], "events": [{"type": "move", "x": 10, "y": 20, "delay": 0.008}, ...]}
  -> {"cmd": "result", "id": 1, "ok": true}
  <- {"id": 1, "logged": true}
  -> {"cmd": "summary"}
  <- {"summary": [{"style": "...", "clicks": 40, "ok": 37, "rate": 0.925, "mean_s": 0.61}, ...]}

The styles file is YAML or JSON: either a list of styles, or a grid.

  styles:
    - {name: precise, path: instant, aim: deepest}
    - {name: me, profile: profiles/me.yaml, path: human, aim: human, speed: 1.5}
  grid:                      # or instead: every combination of these
    profile: intermediate
    path: [human, linear]
    aim: [center, deepest]
    speed: [1, 2]
    pointer_spread: [0.3, 0.6]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from .experiment import MouseStyle, Shape, grid, plan


def load_styles(path: str) -> list[MouseStyle]:
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith((".yaml", ".yml")):
        import yaml

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    styles = [MouseStyle.from_dict(s) for s in data.get("styles", [])]
    if "grid" in data:
        spec = dict(data["grid"])
        base = spec.pop("profile", "intermediate")
        styles += grid(base, **{k: v if isinstance(v, list) else [v] for k, v in spec.items()})
    if not styles:
        raise ValueError(f"{path} defines no styles")
    if len({s.name for s in styles}) != len(styles):
        raise ValueError("style names must be unique")
    return styles


class Bridge:
    def __init__(self, styles: list[MouseStyle], log_path: str | None = None, pick: str = "round-robin",
                 seed: int | None = None):
        self.styles = {s.name: s for s in styles}
        self.order = list(self.styles)
        self.pick = pick
        self.rng = random.Random(seed)
        self.next_id = 0
        self.pending: dict[int, dict] = {}
        self.outcomes: list[dict] = []
        self.log = open(log_path, "a", encoding="utf-8") if log_path else None
        if log_path and Path(log_path).exists():
            for line in Path(log_path).read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.outcomes.append(json.loads(line))  # earlier sessions count too

    def _choose(self, requested: str | None) -> MouseStyle:
        if requested:
            if requested not in self.styles:
                raise ValueError(f"unknown style {requested!r}")
            return self.styles[requested]
        if self.pick == "random":
            return self.styles[self.rng.choice(self.order)]
        return self.styles[self.order[self.next_id % len(self.order)]]

    def handle(self, msg: dict) -> dict:
        cmd = msg.get("cmd")
        if cmd == "click":
            if "polygon" in msg:
                shape = Shape.polygon(msg["polygon"])
            elif "rect" in msg:
                shape = Shape.rect(*msg["rect"])
            else:
                raise ValueError("click needs a rect or a polygon")
            style = self._choose(msg.get("style"))
            self.next_id += 1
            events, aim = plan(style, tuple(msg["from"]), shape, seed=self.rng.randrange(2**31),
                               button=msg.get("button", "left"))
            box = shape.box
            self.pending[self.next_id] = {"style": style.name, "w": box.width, "h": box.height,
                                          "seconds": sum(e.delay_before for e in events)}
            return {"id": self.next_id, "style": style.name, "aim": [round(aim[0], 1), round(aim[1], 1)],
                    "events": [{"type": e.kind, "x": e.x, "y": e.y, "delay": round(e.delay_before, 4),
                                "button": e.button} for e in events]}
        if cmd == "result":
            info = self.pending.pop(msg["id"], None)
            if info is None:
                raise ValueError(f"no click with id {msg['id']} is waiting for a result")
            row = {**info, "ok": bool(msg["ok"]), **({"note": msg["note"]} if "note" in msg else {})}
            self.outcomes.append(row)
            if self.log:
                self.log.write(json.dumps(row) + "\n")
                self.log.flush()
            return {"id": msg["id"], "logged": True}
        if cmd == "summary":
            return {"summary": summarize(self.outcomes)}
        if cmd == "styles":
            return {"styles": self.order}
        raise ValueError(f"unknown cmd {cmd!r}")

    def serve(self, inp=sys.stdin, out=sys.stdout) -> None:
        for line in inp:
            if not line.strip():
                continue
            try:
                reply = self.handle(json.loads(line))
            except Exception as exc:  # report and keep serving
                reply = {"error": str(exc)}
            out.write(json.dumps(reply) + "\n")
            out.flush()


def summarize(outcomes: list[dict]) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for o in outcomes:
        by.setdefault(o["style"], []).append(o)
    rows = [{"style": s, "clicks": len(v), "ok": sum(o["ok"] for o in v),
             "rate": round(sum(o["ok"] for o in v) / len(v), 4),
             "mean_s": round(sum(o["seconds"] for o in v) / len(v), 4)} for s, v in by.items()]
    return sorted(rows, key=lambda r: (-r["rate"], r["mean_s"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("styles", help="styles .yaml or .json file")
    ap.add_argument("--log", help="append each reported result to this .jsonl file")
    ap.add_argument("--pick", choices=["round-robin", "random"], default="round-robin")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--summary", action="store_true", help="print the summary of --log and exit")
    args = ap.parse_args(argv)
    bridge = Bridge(load_styles(args.styles), args.log, args.pick, args.seed)
    if args.summary:
        for row in summarize(bridge.outcomes):
            print(f"{row['style']:<40} {row['ok']:>4}/{row['clicks']:<4} {row['rate']:>6.1%}  {row['mean_s']:.3f}s")
        return
    bridge.serve()


if __name__ == "__main__":
    main()
