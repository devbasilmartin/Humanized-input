"""Human-like timing: keystroke intervals, typos and corrections, listening time.

Nothing here touches a browser. The Humanizer turns a Profile into a plan of
keystrokes and delays, which keeps it easy to unit-test and to study.

Key idea: human inter-key intervals are right-skewed (most keys come quickly,
a few come much later), so they are sampled from a log-normal distribution
whose mean matches the profile's words-per-minute.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

from .profiles import Profile

# Neighbouring keys on a QWERTY layout, used to produce realistic typos.
_QWERTY_ROWS = ["1234567890-=", "qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./"]
_NEIGHBOURS: dict[str, str] = {}
for r, row in enumerate(_QWERTY_ROWS):
    for c, ch in enumerate(row):
        near = []
        for dr in (-1, 0, 1):
            rr = r + dr
            if 0 <= rr < len(_QWERTY_ROWS):
                for dc in (-1, 0, 1):
                    cc = c + dc
                    if (dr, dc) != (0, 0) and 0 <= cc < len(_QWERTY_ROWS[rr]):
                        near.append(_QWERTY_ROWS[rr][cc])
        _NEIGHBOURS[ch] = "".join(near)

CHARS_PER_WORD = 5  # the standard definition used for WPM


class Clock:
    """Tracks simulated time. Subclasses decide whether to really wait."""

    def __init__(self):
        self.elapsed = 0.0

    def sleep(self, seconds: float) -> None:
        self.elapsed += max(0.0, seconds)


class VirtualClock(Clock):
    """Never waits. Use for tests and fast what-if comparisons."""


class RealClock(Clock):
    """Actually waits, optionally sped up (speedup=4 runs 4x faster than life)."""

    def __init__(self, speedup: float = 1.0):
        super().__init__()
        self.speedup = speedup

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        if seconds > 0:
            time.sleep(seconds / self.speedup)


@dataclass(frozen=True)
class KeyStroke:
    key: str  # a character, or a key name such as "Backspace"
    delay_before: float  # seconds to wait before pressing
    is_correction: bool = False


class Humanizer:
    def __init__(self, profile: Profile, rng: random.Random | None = None):
        self.profile = profile
        self.rng = rng or random.Random(profile.seed)

    # --- primitive samples -------------------------------------------------

    def _lognormal(self, mean: float, sigma: float) -> float:
        # Pick mu so that the distribution's mean equals `mean`.
        mu = math.log(mean) - sigma**2 / 2
        return self.rng.lognormvariate(mu, sigma)

    def key_interval(self, prev: str | None = None) -> float:
        mean = 60.0 / (self.profile.typing_wpm * CHARS_PER_WORD)
        interval = self._lognormal(mean, self.profile.key_jitter)
        if prev is not None and (prev == " " or not prev.isalnum()):
            interval *= 1.5  # people pause slightly at word boundaries
        return interval

    def key_hold(self) -> float:
        """How long a key stays pressed (dwell time), typically 70-130 ms."""
        return min(self._lognormal(0.095, 0.25), 0.4)

    def reaction(self) -> float:
        return self._lognormal(self.profile.reaction_s, 0.3)

    def think(self) -> float:
        lo, hi = self.profile.think_s
        return self.rng.uniform(lo, hi)

    def listen_time(self, utterance: str, full: bool = False) -> float:
        """Seconds spent listening to the screen reader say `utterance`.

        Users who skim stop listening after `skim_words` words unless what
        they are hearing is what they were looking for (full=True).
        """
        words = len(utterance.split())
        if not full and self.profile.skim_words is not None:
            words = min(words, self.profile.skim_words)
        return words / self.profile.speech_wpm * 60.0

    # --- typing ------------------------------------------------------------

    def typo_for(self, ch: str) -> str | None:
        near = _NEIGHBOURS.get(ch.lower())
        if not near:
            return None
        wrong = self.rng.choice(near)
        return wrong.upper() if ch.isupper() else wrong

    def plan_typing(self, text: str) -> list[KeyStroke]:
        """Keystrokes (with delays) a person with this profile would produce.

        A typo is a neighbouring key. The typist keeps going for up to
        `typo_notice_max` characters before noticing, pauses, backspaces over
        the damage and retypes. The final text always equals `text`.
        """
        plan: list[KeyStroke] = []
        prev: str | None = None
        i = 0
        while i < len(text):
            ch = text[i]
            wrong = self.typo_for(ch) if self.rng.random() < self.profile.typo_rate else None
            if wrong is None:
                plan.append(KeyStroke(ch, self.key_interval(prev)))
                prev = ch
                i += 1
                continue

            # Type the wrong key, then a few more correct ones before noticing.
            plan.append(KeyStroke(wrong, self.key_interval(prev)))
            carry_on = self.rng.randint(0, self.profile.typo_notice_max)
            carry_on = min(carry_on, len(text) - i - 1)
            for j in range(1, carry_on + 1):
                plan.append(KeyStroke(text[i + j], self.key_interval(text[i + j - 1])))

            # Notice, then backspace (backspacing is faster than typing).
            first = True
            for _ in range(carry_on + 1):
                delay = self.reaction() if first else self.key_interval() * 0.6
                plan.append(KeyStroke("Backspace", delay, is_correction=True))
                first = False
            # Retype from the typo position onward; the loop continues normally.
            prev = None
        return plan


def apply_plan(text_so_far: str, plan: list[KeyStroke]) -> str:
    """Replay a typing plan into a string. Handy for checking plans in tests."""
    buf = list(text_so_far)
    for stroke in plan:
        if stroke.key == "Backspace":
            if buf:
                buf.pop()
        else:
            buf.append(stroke.key)
    return "".join(buf)
