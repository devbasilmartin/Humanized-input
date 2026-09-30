"""Many different users from a few profiles: trade-offs, individuals, practice, fatigue.

The built-in profiles are averages. Real users differ, and the same user
changes: they learn a site, they get tired, they rush or take care. Every
function here takes a Profile and returns a new one, so the result plugs
into SimulatedUser like any other profile and a run stays reproducible
from its seed.

- `emphasize`: the speed-accuracy trade-off. The same person can rush
  (faster, more typos, wider click scatter) or take care (slower, fewer
  mistakes). Fitts's law research calls this a shift along one curve.
- `individual` / `sample_population`: individual differences. People who
  are quick in one way tend to be quick in others, so traits share a
  common "general speed" factor plus their own noise.
- `practiced`: the power law of practice. Time per task falls as
  T(n) = floor + (T(1) - floor) * n^-rate over repeated sessions, fast at
  first and then levelling off. Useful for learnability questions such as
  "how many visits until a novice finishes sign-up in under a minute?"
- `fatigued`: slower responses, more errors and, for tremor, a larger
  wobble as a session runs on.

These make test runs cover a realistic *spread* of users. They are not a
way to make automated traffic pass as real people (see the Scope section
of the README).
"""

from __future__ import annotations

import math
import random
from dataclasses import replace

from .profiles import BUILTIN_PROFILES, Profile, load_profile

# Fields that are times: smaller is faster.
_TIME_FIELDS = ("reaction_s", "pointer_a_s", "pointer_b_s")
# Fields that are rates: larger is faster.
_RATE_FIELDS = ("typing_wpm", "speech_wpm")
# Fields that are error sizes: larger is sloppier.
_ERROR_FIELDS = ("typo_rate", "pointer_spread")


def _scale(profile: Profile, time: float = 1.0, rate: float = 1.0, error: float = 1.0,
           tremor: float = 1.0, **extra) -> dict:
    """Changes to apply to `profile`, as keyword arguments for replace()."""
    lo, hi = profile.think_s
    changes = {f: getattr(profile, f) * time for f in _TIME_FIELDS}
    changes["think_s"] = (lo * time, hi * time)
    changes.update({f: getattr(profile, f) * rate for f in _RATE_FIELDS})
    changes.update({f: getattr(profile, f) * error for f in _ERROR_FIELDS})
    changes["typo_rate"] = min(changes["typo_rate"], 0.5)
    changes["pointer_tremor_px"] = profile.pointer_tremor_px * tremor
    changes.update(extra)
    return changes


def _derived(profile: Profile, suffix: str, note: str, **changes) -> Profile:
    return replace(profile, name=f"{profile.name}-{suffix}", notes=[*profile.notes, note], **changes)


def emphasize(profile: Profile, emphasis: float) -> Profile:
    """Shift a profile along the speed-accuracy trade-off.

    `emphasis` runs from -1 (as careful as possible) to +1 (rushing).
    At +1 actions take about 0.76x as long and errors are about 1.7x as
    likely; at -1, about 1.3x as long and 0.6x the errors.
    """
    if not -1 <= emphasis <= 1:
        raise ValueError("emphasis must be between -1 (careful) and +1 (fast)")
    time = 2 ** (-0.4 * emphasis)
    changes = _scale(profile, time=time, rate=1 / time, error=2 ** (0.8 * emphasis))
    label = "fast" if emphasis > 0 else "careful" if emphasis < 0 else "balanced"
    return _derived(profile, label, f"speed-accuracy emphasis {emphasis:+.2f}", **changes)


def individual(profile: Profile, seed: int, spread: float = 0.15) -> Profile:
    """One plausible person drawn around `profile`.

    `spread` is the log-scale standard deviation of each trait (0.15 is
    roughly +/-15%). A shared "general speed" factor makes fast typists
    tend to be fast mouse users and fast deciders too.
    """
    rng = random.Random(seed)
    g = rng.gauss(0, 1)  # general speed, shared by all speed traits

    def factor(shared: float = 0.7) -> float:
        own = math.sqrt(1 - shared**2)
        return math.exp(spread * (shared * g + own * rng.gauss(0, 1)))

    changes = {f: getattr(profile, f) / factor() for f in _TIME_FIELDS}
    t = 1 / factor()
    changes["think_s"] = (profile.think_s[0] * t, profile.think_s[1] * t)
    changes.update({f: getattr(profile, f) * factor() for f in _RATE_FIELDS})
    # Accuracy is only loosely tied to speed.
    changes.update({f: getattr(profile, f) * factor(shared=0.0) for f in _ERROR_FIELDS})
    changes["typo_rate"] = min(changes["typo_rate"], 0.5)
    changes["pointer_curvature"] = profile.pointer_curvature * factor(shared=0.0)
    changes["pointer_tremor_px"] = profile.pointer_tremor_px * factor(shared=0.0)
    return _derived(profile, f"{seed:04d}", f"individual drawn with seed {seed}, spread {spread}",
                    seed=seed, **changes)


def sample_population(n: int, mix: dict[str, float] | None = None, seed: int = 0,
                      spread: float = 0.15) -> list[Profile]:
    """`n` different users. `mix` gives the share of each base profile, by
    built-in name or profile file path, e.g. {"expert": 0.3, "novice": 0.7}."""
    mix = mix or {name: 1.0 for name in BUILTIN_PROFILES}
    rng = random.Random(seed)
    names, weights = zip(*mix.items())
    bases = rng.choices(names, weights=weights, k=n)
    loaded = {name: load_profile(name) for name in names}
    return [individual(loaded[b], seed * 100_003 + i, spread) for i, b in enumerate(bases)]


def practiced(profile: Profile, sessions: int, rate: float = 0.35, floor: float = 0.5) -> Profile:
    """The same user after `sessions` visits (1 = first visit, unchanged).

    Times and error rates fall along a power law toward `floor` times their
    first-visit values; typing and speech get faster by the same factor.
    Navigation strategy and tremor do not change with practice.
    """
    if sessions < 1:
        raise ValueError("sessions starts at 1 (the first visit)")
    f = floor + (1 - floor) * sessions ** -rate
    changes = _scale(profile, time=f, rate=1 / f, error=f)
    return _derived(profile, f"visit{sessions}", f"after {sessions} sessions (power law, rate {rate})",
                    **changes)


def fatigued(profile: Profile, minutes: float, per_hour: float = 0.2, cap: float = 1.6) -> Profile:
    """The same user `minutes` into a session: each hour adds `per_hour`
    to their times, errors and tremor, up to `cap` times the rested values."""
    if minutes < 0:
        raise ValueError("minutes must be >= 0")
    f = min(1 + per_hour * minutes / 60, cap)
    changes = _scale(profile, time=f, rate=1 / f, error=f, tremor=f)
    return _derived(profile, f"{minutes:g}min", f"fatigue after {minutes:g} minutes", **changes)
