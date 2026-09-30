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
    ),
    "motor": Profile(
        name="motor",
        description="Experienced screen reader user with a motor impairment: "
        "slow, effortful typing but efficient listening.",
        typing_wpm=12,
        key_jitter=0.5,
        typo_rate=0.05,
        speech_wpm=320,
        skim_words=3,
        reaction_s=1.0,
        think_s=(1.0, 2.5),
        navigation="tab",
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
