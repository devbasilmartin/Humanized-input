"""User profiles: the numbers that make a simulated screen reader user behave
like a particular kind of person.

Every field maps to something you can observe in real screen reader users:

- typing_wpm / key_jitter / typo_rate: how they type once they reach a field.
- speech_wpm: how fast their screen reader talks. Experienced users commonly
  run speech at 300-450+ words per minute; newer users are closer to 150-200.
- skim_words: experienced users interrupt speech as soon as they recognise
  something is not what they want. None means "listens to everything".
- reaction_s / think_s: time to react to what was heard and to decide what to
  do next.
- navigation: the strategy they use to find things (see user.py).
- pointer_*: how they use a mouse, when they use one (see pointer.py).
  pointer_a_s / pointer_b_s are the Fitts's law constants (seconds, and
  seconds per bit of difficulty); pointer_spread scales how widely clicks
  scatter; pointer_curvature how much paths bow; pointer_tremor_px the
  amplitude of hand tremor; pointer_settle_s the pause on the target before
  pressing and pointer_hold_s how long the button stays down. Most blind users do not use a mouse at all;
  these matter for low-vision, motor-impaired and sighted keyboard+mouse users.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

NAVIGATION_STRATEGIES = ("quicknav", "tab", "linear")


@dataclass(frozen=True)
class Profile:
    name: str
    description: str = ""
    typing_wpm: float = 40.0
    key_jitter: float = 0.35
    typo_rate: float = 0.03
    typo_notice_max: int = 2
    speech_wpm: float = 300.0
    skim_words: int | None = 4
    reaction_s: float = 0.4
    think_s: tuple[float, float] = (0.5, 1.5)
    navigation: str = "tab"
    pointer_a_s: float = 0.10
    pointer_b_s: float = 0.15
    pointer_spread: float = 1.0
    pointer_curvature: float = 0.08
    pointer_tremor_px: float = 0.0
    pointer_settle_s: float = 0.12
    pointer_hold_s: float = 0.095
    max_steps: int = 80
    seed: int | None = None
    notes: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.navigation not in NAVIGATION_STRATEGIES:
            raise ValueError(
                f"navigation must be one of {NAVIGATION_STRATEGIES}, got {self.navigation!r}"
            )
        if self.typing_wpm <= 0 or self.speech_wpm <= 0:
            raise ValueError("typing_wpm and speech_wpm must be positive")
        if not 0 <= self.typo_rate < 1:
            raise ValueError("typo_rate must be in [0, 1)")
        lo, hi = self.think_s
        if lo < 0 or hi < lo:
            raise ValueError("think_s must be (low, high) with 0 <= low <= high")
        if self.pointer_a_s < 0 or self.pointer_b_s <= 0:
            raise ValueError("pointer_a_s must be >= 0 and pointer_b_s > 0")
        if self.pointer_spread < 0 or self.pointer_tremor_px < 0:
            raise ValueError("pointer_spread and pointer_tremor_px must be >= 0")
        if self.pointer_settle_s <= 0 or self.pointer_hold_s <= 0:
            raise ValueError("pointer_settle_s and pointer_hold_s must be > 0")

    def with_seed(self, seed: int) -> "Profile":
        return replace(self, seed=seed)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["think_s"] = list(self.think_s)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        data = dict(data)
        if "think_s" in data:
            data["think_s"] = tuple(data["think_s"])
        return cls(**data)


BUILTIN_PROFILES: dict[str, Profile] = {
    "expert": Profile(
        name="expert",
        description="Daily NVDA/JAWS user. Fast speech, jumps with quick-nav keys "
        "(F for form field, B for button, K for link), interrupts speech early.",
        typing_wpm=70,
        key_jitter=0.30,
        typo_rate=0.02,
        typo_notice_max=1,
        speech_wpm=450,
        skim_words=2,
        reaction_s=0.25,
        think_s=(0.2, 0.6),
        navigation="quicknav",
        pointer_a_s=0.08,
        pointer_b_s=0.11,
    ),
    "intermediate": Profile(
        name="intermediate",
        description="Comfortable user who mostly Tabs between controls.",
        typing_wpm=45,
        typo_rate=0.03,
        speech_wpm=300,
        skim_words=4,
        reaction_s=0.4,
        think_s=(0.5, 1.5),
        navigation="tab",
    ),
    "novice": Profile(
        name="novice",
        description="Recently started using a screen reader. Default speech rate, "
        "reads everything line by line with the down arrow.",
        typing_wpm=25,
        key_jitter=0.45,
        typo_rate=0.06,
        typo_notice_max=3,
        speech_wpm=180,
        skim_words=None,
        reaction_s=0.7,
        think_s=(1.0, 3.0),
        navigation="linear",
        pointer_a_s=0.15,
        pointer_b_s=0.20,
        pointer_spread=1.3,
        pointer_curvature=0.12,
    ),
    "motor": Profile(
        name="motor",
        description="Experienced screen reader user with a motor impairment: "
        "slow, effortful typing but efficient listening. Uses a mouse with a "
        "tremor, so small targets are hard to click.",
        typing_wpm=12,
        key_jitter=0.5,
        typo_rate=0.05,
        speech_wpm=320,
        skim_words=3,
        reaction_s=1.0,
        think_s=(1.0, 2.5),
        navigation="tab",
        pointer_a_s=0.30,
        pointer_b_s=0.35,
        pointer_spread=1.6,
        pointer_curvature=0.15,
        pointer_tremor_px=4.0,
    ),
}


def load_profile(name_or_path: str) -> Profile:
    """Load a built-in profile by name, or a profile from a .json/.yaml file."""
    if name_or_path in BUILTIN_PROFILES:
        return BUILTIN_PROFILES[name_or_path]

    path = Path(name_or_path)
    if not path.exists():
        known = ", ".join(BUILTIN_PROFILES)
        raise FileNotFoundError(f"No built-in profile or file named {name_or_path!r} (built-ins: {known})")

    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        import yaml  # optional dependency

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return Profile.from_dict(data)
