"""Simulated screen reader users sign up in a real Windows desktop app.

Launches examples/windows/signup_form.ps1 (a WinForms window) once per
profile, has the user fill it in with real, human-paced keystrokes, and prints
how it went plus an accessibility audit of the window.

    python examples\\windows\\signup_demo.py --speak
    python examples\\windows\\signup_demo.py --profiles novice --tts        # hear it (pyttsx3)
    python examples\\windows\\signup_demo.py --nvda path\\to\\nvdaControllerClient.dll
    python examples\\windows\\signup_demo.py --speedup 3                    # 3x faster than life

Do not touch the keyboard while it runs: the keystrokes are real.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from humanized_input import BUILTIN_PROFILES, PrintSpeech, RealClock, audit  # noqa: E402
from humanized_input.desktop import desktop_session  # noqa: E402

FORM = Path(__file__).with_name("signup_form.ps1")
LAUNCH = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(FORM)]
TITLE = r"^(Sign up|Welcome) - Example Library$"


def make_speech(args):
    if args.nvda:
        from humanized_input.nvda import NvdaSpeech
        return NvdaSpeech(args.nvda)
    if args.tts:
        from humanized_input.screen_reader import Pyttsx3Speech
        return Pyttsx3Speech()
    return PrintSpeech() if args.speak else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", nargs="*", default=list(BUILTIN_PROFILES))
    ap.add_argument("--speak", action="store_true", help="print each announcement")
    ap.add_argument("--tts", action="store_true", help="speak announcements with pyttsx3")
    ap.add_argument("--nvda", metavar="DLL", help="speak through a running NVDA")
    ap.add_argument("--speedup", type=float, default=1.0)
    args = ap.parse_args()
    speech = make_speech(args)
    no_console = {"creationflags": subprocess.CREATE_NO_WINDOW}

    issues = None
    for name in args.profiles:
        profile = BUILTIN_PROFILES[name].with_seed(42)
        print(f"\n=== {profile.name}: {profile.description}")
        with desktop_session(profile, TITLE, launch=LAUNCH, speech=speech,
                             clock=RealClock(args.speedup), popen_kwargs=no_console) as user:
            if issues is None:
                issues = audit(user.sr.title, user.sr.items, kind="desktop")
            user.sr.read_window()
            user.fill("Full name", "Ada Lovelace")
            user.fill("Email", "ada@example.org")
            user.fill("Library card", "12345678")
            user.check("agree to the terms")
            user.press("Create account")
            print(user.report.summary())

    print("\nAccessibility issues in the window:")
    for issue in issues or []:
        heard = f'  (heard as: "{issue.heard_as}")' if issue.heard_as else ""
        print(f"  - [{issue.rule}] {issue.message}{heard}")


if __name__ == "__main__":
    main()
