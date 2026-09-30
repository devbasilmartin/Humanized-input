"""How often does each profile hit a target of each size with the mouse?

    python examples/target_sizes.py
    python examples/target_sizes.py --headed --real 1 --profile motor   # watch it in a browser

The motor profile has a hand tremor, so small targets are missed more often,
which is why WCAG 2.5.8 asks for targets of at least 24x24 CSS pixels.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from humanized_input import BUILTIN_PROFILES, Humanizer, PointerPlanner, RealClock, Rect
from humanized_input.pointer import click_landed

SIZES = [8, 12, 16, 24, 32, 44]


def table(trials: int):
    print("profile        " + "".join(f"{s:>5}px" for s in SIZES) + "   (hit rate from 700 px away)")
    for name, profile in BUILTIN_PROFILES.items():
        row = []
        for size in SIZES:
            box = Rect(700, 400, size, size)
            hits = sum(click_landed(PointerPlanner(Humanizer(profile.with_seed(s))).plan_click((0, 400), box), box)
                       for s in range(trials))
            row.append(f"{hits / trials:>6.0%} ")
        print(f"{name:<15}" + "".join(row))


def browser_demo(profile_name: str, real: float, headed: bool):
    from humanized_input.session import user_session

    buttons = "".join(
        f'<button style="position:absolute;left:{120 + i * 110}px;top:{150 + (i % 2) * 120}px;'
        f'width:{s}px;height:{s}px;padding:0" onclick="this.textContent=\'✓\'">{s}</button>'
        for i, s in enumerate(SIZES))
    url = f"data:text/html,<title>Targets</title>{buttons}"
    with user_session(url, BUILTIN_PROFILES[profile_name].with_seed(1), clock=RealClock(real),
                      headless=not headed) as user:
        for size in SIZES:
            user.click(str(size))
        print(user.report.summary())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--profile", default="motor", help="profile for the browser demo")
    ap.add_argument("--real", type=float, metavar="SPEEDUP", help="run the browser demo in real time")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    args = ap.parse_args()
    table(args.trials)
    if args.real or args.headed:
        browser_demo(args.profile, args.real or 1.0, args.headed)


if __name__ == "__main__":
    main()
