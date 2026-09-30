"""Run the same sign-up task as each built-in profile and compare.

    python examples/compare_profiles.py            # fast, simulated time
    python examples/compare_profiles.py --speak    # print speech as it happens
    python examples/compare_profiles.py --real 4   # real waits, 4x faster than life
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from humanized_input import BUILTIN_PROFILES, PrintSpeech, RealClock, VirtualClock, audit
from humanized_input.session import user_session

PAGE = (Path(__file__).parent / "signup.html").resolve().as_uri()


def run(profile_name: str, speak: bool, real: float | None, headed: bool):
    clock = RealClock(speedup=real) if real else VirtualClock()
    speech = PrintSpeech() if speak else None
    profile = BUILTIN_PROFILES[profile_name].with_seed(42)

    with user_session(PAGE, profile, speech=speech, clock=clock, headless=not headed) as user:
        if speak:
            print(f"\n=== {profile.name}: {profile.description}")
        user.sr.read_title()
        user.fill("Full name", "Ada Lovelace")
        user.fill("Email", "ada@example.org")
        user.fill("Library card", "12345678")
        user.check("agree to the terms")
        user.press("Create account")
        return user.report, audit(user.sr.title, user.sr.items), user.sr.transcript


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", nargs="*", default=list(BUILTIN_PROFILES))
    ap.add_argument("--speak", action="store_true", help="print each announcement")
    ap.add_argument("--real", type=float, metavar="SPEEDUP", help="really wait, sped up by SPEEDUP")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    args = ap.parse_args()

    for name in args.profiles:
        report, _, _ = run(name, args.speak, args.real, args.headed)
        print(report.summary())

    # Audit the untouched page once.
    with user_session(PAGE, "expert") as user:
        issues = audit(user.sr.title, user.sr.items)
    print("\nAccessibility issues on the page:")
    for issue in issues:
        heard = f'  (heard as: "{issue.heard_as}")' if issue.heard_as else ""
        print(f"  - [{issue.rule}] {issue.message}{heard}")


if __name__ == "__main__":
    main()
