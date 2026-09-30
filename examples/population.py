"""Run the sign-up task for a varied population, then a novice learning curve.

    python examples/population.py --users 40
    python examples/population.py --mix expert=1 novice=3 --users 20

Times are simulated (VirtualClock), so this runs in seconds.
"""

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from humanized_input import BUILTIN_PROFILES
from humanized_input.population import emphasize, practiced, sample_population
from humanized_input.session import user_session

PAGE = (Path(__file__).parent / "signup.html").resolve().as_uri()


def signup(profile) -> tuple[bool, float]:
    with user_session(PAGE, profile) as user:
        ok = all([user.fill("Full name", "Ada Lovelace"), user.fill("Email", "ada@example.org"),
                  user.check("agree to the terms"), user.press("Create account")])
        return ok, user.report.elapsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=30)
    ap.add_argument("--mix", nargs="*", default=[], metavar="NAME=WEIGHT")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    mix = {k: float(v) for k, v in (m.split("=") for m in args.mix)} or None

    by_base: dict[str, list[tuple[bool, float]]] = {}
    for person in sample_population(args.users, mix, seed=args.seed):
        by_base.setdefault(person.name.rsplit("-", 1)[0], []).append(signup(person))
    print(f"{'base profile':<14}{'users':>6}{'success':>9}{'median s':>10}{'slowest s':>11}")
    for base, runs in sorted(by_base.items()):
        times = [t for _, t in runs]
        print(f"{base:<14}{len(runs):>6}{sum(ok for ok, _ in runs) / len(runs):>9.0%}"
              f"{statistics.median(times):>10.1f}{max(times):>11.1f}")

    print("\nSpeed-accuracy trade-off (intermediate):")
    for e in (-1, 0, 1):
        p = emphasize(BUILTIN_PROFILES["intermediate"].with_seed(args.seed), e)
        print(f"  {p.name:<22} {signup(p)[1]:6.1f}s")

    print("\nNovice learning curve (power law of practice):")
    novice = BUILTIN_PROFILES["novice"].with_seed(args.seed)
    for visit in (1, 2, 3, 5, 10, 20):
        print(f"  visit {visit:>2}: {signup(practiced(novice, visit))[1]:6.1f}s")


if __name__ == "__main__":
    main()
