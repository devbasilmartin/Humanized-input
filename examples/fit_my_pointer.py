"""Fit a pointer profile to your own clicks, and see how close it gets.

1. Record: open examples/pointer_recorder.html (or the published recorder)
   and download the JSON at the end.
2. Fit:    python examples/fit_my_pointer.py pointer-recording.json --save profiles/me.yaml

Only the pointer_* fields are fitted; typing and listening come from --base.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from humanized_input.fit import compare, fit_profile, load_recording


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("recording", nargs="+", help="recorder JSON file(s); several rounds fit better")
    ap.add_argument("--base", default="intermediate", help="profile for the non-pointer traits")
    ap.add_argument("--name", default="me")
    ap.add_argument("--save", metavar="PATH", help="write the fitted profile as .yaml or .json")
    args = ap.parse_args()

    trials = [t for path in args.recording for t in load_recording(path)]
    profile, recorded, simulated = fit_profile(trials, args.base, name=args.name)
    print(f"{len(trials)} clicks\n")
    print(compare(recorded, simulated))
    print("\nFitted pointer settings:")
    for key, value in profile.to_dict().items():
        if key.startswith("pointer_"):
            print(f"  {key:<20}{value:.3f}")
    if args.save:
        data = profile.to_dict()
        out = Path(args.save)
        if out.suffix in (".yaml", ".yml"):
            import yaml

            out.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        else:
            import json

            out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"\nSaved to {out}; load it with load_profile({str(out)!r})")


if __name__ == "__main__":
    main()
