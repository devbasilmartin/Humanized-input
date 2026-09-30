"""Hear any Windows app the way a screen reader user would.

Attaches to a window, reads everything in it (like NVDA's object navigation),
runs the audit, and optionally Tabs through it announcing each control.

    python examples\\windows\\explore_window.py --title ".*Notepad$"
    python examples\\windows\\explore_window.py --title "Calculator" --tab 15
    python examples\\windows\\explore_window.py --foreground        # 3 s to click the window you want

Reading is passive. --tab sends real Tab key presses to the window.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from humanized_input import BUILTIN_PROFILES, PrintSpeech, RealClock, SimulatedUser, audit  # noqa: E402
from humanized_input.backends.windows_uia import WindowsUIABackend  # noqa: E402
from humanized_input.desktop import find_window  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--title", help="regular expression for the window title")
    group.add_argument("--foreground", action="store_true", help="use the window in front after 3 seconds")
    ap.add_argument("--tab", type=int, default=0, metavar="N", help="press Tab N times, announcing focus")
    ap.add_argument("--profile", default="intermediate", choices=list(BUILTIN_PROFILES))
    args = ap.parse_args()

    if args.foreground:
        print("Click the window to explore...")
        time.sleep(3)
        import uiautomation as auto
        window = auto.GetForegroundControl()
    else:
        window = find_window(args.title)

    backend = WindowsUIABackend(window)
    backend.bring_to_front()
    user = SimulatedUser(backend, BUILTIN_PROFILES[args.profile], speech=PrintSpeech(), clock=RealClock())

    print("\n--- Reading the window")
    user.sr.read_title()
    user.sr.say_all()

    if args.tab:
        print(f"\n--- Tabbing {args.tab} times")
        for _ in range(args.tab):
            user.shortcut("Tab")
            user.sr.announce_focus()

    print("\n--- Audit")
    for issue in audit(user.sr.title, user.sr.items, kind="desktop"):
        heard = f'  (heard as: "{issue.heard_as}")' if issue.heard_as else ""
        print(f"  - [{issue.rule}] {issue.message}{heard}")


if __name__ == "__main__":
    main()
