"""Simulated screen reader users with human-like input, for accessibility study and testing."""

from .audit import Issue, audit
from .pointer import PointerPlanner, Rect
from .population import emphasize, fatigued, individual, practiced, sample_population
from .profiles import BUILTIN_PROFILES, Profile, load_profile
from .screen_reader import PrintSpeech, Speech, VirtualScreenReader, describe
from .timing import Humanizer, RealClock, VirtualClock
from .user import SessionReport, SimulatedUser
from .backends import Backend

__all__ = [
    "BUILTIN_PROFILES", "Backend", "Humanizer", "Issue", "PointerPlanner", "PrintSpeech", "Profile",
    "RealClock", "Rect",
    "SessionReport", "SimulatedUser", "Speech", "VirtualClock", "VirtualScreenReader",
    "audit", "describe", "emphasize", "fatigued", "individual", "load_profile", "practiced",
    "sample_population",
]
