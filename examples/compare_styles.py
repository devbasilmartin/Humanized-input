"""Compare mouse styles in simulation before trying them for real.

    python examples/compare_styles.py                      # the styles in examples/mouse_styles.yaml
    python examples/compare_styles.py my_styles.yaml --trials 500

Targets: a small square, a thin L shape and a ring, whose centres are off
the shape. Put your own outlines in TARGETS to test the shapes you click.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from humanized_input.bridge import load_styles
from humanized_input.experiment import Shape, evaluate, table

TARGETS = [
    Shape.rect(600, 400, 24, 24),
    Shape.polygon([(600, 400), (612, 400), (612, 440), (650, 440), (650, 452), (600, 452)]),
    Shape.polygon([(700, 300), (760, 300), (760, 360), (700, 360), (700, 300),
                   (712, 312), (712, 348), (748, 348), (748, 312), (712, 312)]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("styles", nargs="?", default=str(Path(__file__).parent / "mouse_styles.yaml"))
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--sort", choices=["hit_rate", "time"], default="hit_rate")
    args = ap.parse_args()
    print(table(evaluate(load_styles(args.styles), TARGETS, trials=args.trials), sort=args.sort))


if __name__ == "__main__":
    main()
