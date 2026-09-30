"""A simulated user writes a note in Notepad, then opens and cancels Save As.

Shows humanized typing (speed, typos, corrections) in a real app, and how a
screen reader announces a dialog when it opens.

    python examples\\windows\\notepad_demo.py --profile novice

Notepad is left open afterwards; close it and choose "Don't save".
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from humanized_input import BUILTIN_PROFILES, PrintSpeech  # noqa: E402
from humanized_input.desktop import desktop_session  # noqa: E402

TEXT = "Shopping list: milk, eggs, bread and a new pair of headphones."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="intermediate", choices=list(BUILTIN_PROFILES))
    args = ap.parse_args()
    profile = BUILTIN_PROFILES[args.profile]

    with desktop_session(profile, r".*Notepad$", launch=["notepad.exe"],
                         speech=PrintSpeech(), close=False) as user:
        user.sr.read_window()
        user.sr.announce_focus()   # the text area has focus when Notepad opens
        user.type(TEXT)
        user.shortcut("Control+Shift+s")   # Save As: the dialog gets announced
        user.shortcut("Escape")            # changed their mind
        print("\n" + user.report.summary())


if __name__ == "__main__":
    main()
