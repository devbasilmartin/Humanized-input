"""A folder of saved mouse styles, and a command line to manage and compare them.

Each style is one YAML file with its profile inline, so a file is complete on
its own and easy to edit by hand. The bridge accepts the folder directly.

    python -m humanized_input.styles starter               # save the starter styles
    python -m humanized_input.styles list
    python -m humanized_input.styles show precise
    python -m humanized_input.styles new careful --from precise --set speed=0.7 --set pointer_spread=0.3
    python -m humanized_input.styles new me --profile profiles/me.yaml --set aim=deepest
    python -m humanized_input.styles edit careful --set lead=true --description "slow, leads moving targets"
    python -m humanized_input.styles compare                # all saved styles, in simulation
    python -m humanized_input.styles compare precise careful --trials 500
    python -m humanized_input.styles delete careful
    python -m humanized_input.bridge styles/ --log outcomes.jsonl

`--dir` picks the folder (default: ./styles). `--set` takes any style field
(path, aim, speed, aim_jitter_px, lead, clicks, button, ease_s) or any
profile field (pointer_spread, pointer_flick_s, ...).
"""

from __future__ import annotations

import argparse
import re
from dataclasses import fields, replace
from pathlib import Path

import yaml

from .experiment import MouseStyle, Shape, evaluate, table
from .profiles import Profile, load_profile

STYLE_FIELDS = {f.name for f in fields(MouseStyle)} - {"name", "profile"}

# Every setting a style can change, for the `params` command and the UI.
# (key, group, kind, choices or (min, max, step), help)
PARAMS = [
    ("path", "Style", "choice", ["human", "linear", "ease", "instant"],
     "How the pointer travels: the full hand model, a straight line, a pyautogui-style eased glide, or a jump."),
    ("aim", "Style", "choice", ["human", "center", "middle", "centroid", "deepest", "uniform"],
     "Where in the target to click. deepest is the point furthest from every edge."),
    ("speed", "Style", "number", (0.25, 4.0, 0.05), "Pace multiplier: 2 means everything takes half as long."),
    ("aim_jitter_px", "Style", "number", (0.0, 20.0, 0.5), "Extra random offset added to the aim point, in pixels."),
    ("lead", "Style", "bool", None, "For moving targets, aim where the target will be mid-press."),
    ("clicks", "Style", "int", (1, 3, 1), "Clicks per target: 2 for a double click."),
    ("button", "Style", "choice", ["left", "right", "middle"], "Which mouse button."),
    ("ease_s", "Style", "range", (0.0, 1.0, 0.01), "ease path only: the glide takes a random time in this range (s)."),
    ("pointer_a_s", "Timing", "number", (0.0, 0.6, 0.005), "Fitts's law intercept: fixed time per move (s)."),
    ("pointer_b_s", "Timing", "number", (0.02, 0.6, 0.005), "Fitts's law slope: extra time per bit of difficulty (s)."),
    ("pointer_flick_s", "Timing", "number", (0.0, 0.6, 0.005),
     "Length of the first fast stroke (s). 0 uses a share of the Fitts time instead (primary_frac)."),
    ("pointer_primary_frac", "Timing", "number", (0.05, 1.0, 0.01),
     "When flick_s is 0: the first stroke's share of the Fitts time."),
    ("pointer_settle_s", "Timing", "number", (0.0, 0.6, 0.005), "Pause on a 24 px target before pressing (s)."),
    ("pointer_settle_exp", "Timing", "number", (0.0, 2.0, 0.01), "How much longer that pause gets on smaller targets."),
    ("pointer_hold_s", "Timing", "number", (0.01, 0.5, 0.005), "How long the button stays down (s)."),
    ("pointer_spread", "Accuracy", "number", (0.0, 2.0, 0.01),
     "Aim scatter that grows with target size (1.0 = SD of width / 4.133). Used by aim=human."),
    ("pointer_noise_px", "Accuracy", "number", (0.0, 6.0, 0.05), "Aim scatter that stays the same at any size (px SD)."),
    ("pointer_stop_tol", "Accuracy", "number", (0.0, 1.0, 0.01),
     "How close to the aim point (as a share of target size) before stopping corrections and clicking."),
    ("pointer_tremor_px", "Accuracy", "number", (0.0, 10.0, 0.1), "Hand tremor amplitude, also while the button is held (px)."),
    ("pointer_curvature", "Path shape", "number", (0.0, 0.5, 0.005), "How much paths bow to one side."),
    ("pointer_reach", "Path shape", "number", (0.3, 1.1, 0.01), "Share of the distance the first stroke covers."),
    ("pointer_reach_sd", "Path shape", "number", (0.0, 0.4, 0.005),
     "How much that reach varies; short first strokes need a visible second correction."),
]
PROFILE_FIELDS = {f.name for f in fields(Profile)} - {"name"}

STARTER = [
    MouseStyle("skillbot-classic", path="ease", aim="middle",
               description="what skillbot does today: a quick eased glide into the box's middle"),
    MouseStyle("precise", path="instant", aim="deepest", description="jump to the deepest point"),
    MouseStyle("precise-glide", path="linear", aim="deepest", description="straight line to the deepest point"),
    MouseStyle("human", path="human", aim="human", description="the full pointer model, human scatter"),
    MouseStyle("human-deepest", path="human", aim="deepest", description="the pointer model, aimed at the deepest point"),
    MouseStyle("human-lead", path="human", aim="deepest", lead=True,
               description="the pointer model, leading moving targets"),
]


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    if not slug:
        raise ValueError(f"style name {name!r} has no usable characters")
    return slug


def apply_changes(style: MouseStyle, changes: dict) -> MouseStyle:
    """Set style fields and profile fields by name."""
    style_changes, profile_changes = {}, {}
    for key, value in changes.items():
        if key in STYLE_FIELDS:
            style_changes[key] = tuple(value) if key == "ease_s" else value
        elif key in PROFILE_FIELDS:
            profile_changes[key] = tuple(value) if key == "think_s" else value
        else:
            raise ValueError(f"unknown setting {key!r}; style settings: {', '.join(sorted(STYLE_FIELDS))}; "
                             "or any profile field such as pointer_spread")
    if profile_changes:
        style_changes["profile"] = replace(style.profile, **profile_changes)
    return replace(style, **style_changes)


class StyleLibrary:
    def __init__(self, folder: str | Path = "styles"):
        self.folder = Path(folder)

    def path_for(self, name: str) -> Path:
        return self.folder / f"{_slug(name)}.yaml"

    def names(self) -> list[str]:
        if not self.folder.is_dir():
            return []
        return sorted(yaml.safe_load(p.read_text(encoding="utf-8"))["name"]
                      for p in self.folder.glob("*.yaml"))

    def load(self, name: str) -> MouseStyle:
        path = self.path_for(name)
        if not path.exists():
            raise KeyError(f"no saved style {name!r} in {self.folder}/ (have: {', '.join(self.names()) or 'none'})")
        return MouseStyle.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")))

    def save(self, style: MouseStyle, overwrite: bool = False) -> Path:
        path = self.path_for(style.name)
        if path.exists() and not overwrite:
            raise FileExistsError(f"style {style.name!r} already exists; use edit, or delete it first")
        self.folder.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(style.to_dict(), sort_keys=False), encoding="utf-8")
        return path

    def delete(self, name: str) -> None:
        path = self.path_for(name)
        if not path.exists():
            raise KeyError(f"no saved style {name!r}")
        path.unlink()


# Simulation targets for `compare`: a small square, a thin L, a ring, and a
# square walking sideways at 120 px/s (about an NPC's pace when zoomed out).
COMPARE_TARGETS = [
    Shape.rect(600, 400, 24, 24),
    Shape.polygon([(600, 400), (612, 400), (612, 440), (650, 440), (650, 452), (600, 452)]),
    Shape.polygon([(700, 300), (760, 300), (760, 360), (700, 360), (700, 300),
                   (712, 312), (712, 348), (748, 348), (748, 312), (712, 312)]),
    (Shape.rect(500, 300, 30, 40), (120.0, 0.0)),
]


def _parse_sets(pairs: list[str]) -> dict:
    out = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"--set needs key=value, got {pair!r}")
        out[key.strip()] = yaml.safe_load(value)
    return out


def _summary(style: MouseStyle) -> str:
    extra = [f"x{style.speed:g}"] + (["lead"] if style.lead else []) + \
            ([f"{style.clicks} clicks"] if style.clicks > 1 else []) + \
            ([style.button] if style.button != "left" else [])
    return f"{style.name:<22} {style.path:<8} aim={style.aim:<9} {' '.join(extra):<14} {style.description}"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Manage and compare saved mouse styles.")
    ap.add_argument("--dir", default="styles", help="the style folder (default: ./styles)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("params", help="every setting --set accepts, with its range")
    sub.add_parser("starter", help="save the starter styles (skips names that exist)")
    p = sub.add_parser("show"); p.add_argument("name")
    p = sub.add_parser("delete"); p.add_argument("name")
    for cmd in ("new", "edit"):
        p = sub.add_parser(cmd)
        p.add_argument("name")
        p.add_argument("--set", action="append", metavar="KEY=VALUE", help="a style or profile setting")
        p.add_argument("--description")
        if cmd == "new":
            p.add_argument("--from", dest="source", help="copy this saved style")
            p.add_argument("--profile", help="start from a built-in profile or a profile file")
    p = sub.add_parser("compare")
    p.add_argument("names", nargs="*", help="saved styles (default: all)")
    p.add_argument("--trials", type=int, default=400)
    p.add_argument("--sort", choices=["hit_rate", "time"], default="hit_rate")
    args = ap.parse_args(argv)
    lib = StyleLibrary(args.dir)

    if args.cmd == "params":
        group = None
        for key, grp, kind, spec, help_ in PARAMS:
            if grp != group:
                print(f"\n{grp}")
                group = grp
            rng = "/".join(spec) if kind == "choice" else "true/false" if kind == "bool" else \
                f"{spec[0]:g}-{spec[1]:g}" + (" (low, high)" if kind == "range" else "")
            print(f"  {key:<22}{rng:<48}{help_}")
    elif args.cmd == "list":
        names = lib.names()
        print("\n".join(_summary(lib.load(n)) for n in names) if names else
              f"no styles in {lib.folder}/ yet; `starter` saves a few to begin with")
    elif args.cmd == "starter":
        have = set(lib.names())
        for style in STARTER:
            if style.name not in have:
                lib.save(style)
                print(f"saved {style.name}")
    elif args.cmd == "show":
        print(lib.path_for(args.name).read_text(encoding="utf-8") if lib.path_for(args.name).exists()
              else f"no saved style {args.name!r}")
    elif args.cmd == "delete":
        lib.delete(args.name)
        print(f"deleted {args.name}")
    elif args.cmd == "new":
        if args.source:
            style = replace(lib.load(args.source), name=args.name, description="")
        else:
            style = MouseStyle(args.name, profile=load_profile(args.profile or "intermediate"))
        style = apply_changes(style, _parse_sets(args.set))
        if args.description is not None:
            style = replace(style, description=args.description)
        print(f"saved {lib.save(style)}")
    elif args.cmd == "edit":
        style = apply_changes(lib.load(args.name), _parse_sets(args.set))
        if args.description is not None:
            style = replace(style, description=args.description)
        print(f"saved {lib.save(style, overwrite=True)}")
    elif args.cmd == "compare":
        names = args.names or lib.names()
        if not names:
            raise SystemExit(f"no styles in {lib.folder}/ to compare")
        print(table(evaluate([lib.load(n) for n in names], COMPARE_TARGETS, trials=args.trials), sort=args.sort))


if __name__ == "__main__":
    main()
