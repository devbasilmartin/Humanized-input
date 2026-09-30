import statistics

import pytest

from humanized_input import BUILTIN_PROFILES, SimulatedUser
from humanized_input.pointer import PointerPlanner, Rect, click_landed
from humanized_input.population import emphasize, fatigued, individual, practiced, sample_population
from humanized_input.timing import Humanizer

from test_desktop import FakeSignupApp

BOX = Rect(600, 400, 14, 14)


def mean_click(profile, n=200):
    """(mean seconds, hit rate) for clicking BOX from 700 px away."""
    times, hits = [], 0
    for s in range(n):
        events = PointerPlanner(Humanizer(profile.with_seed(s))).plan_click((0, 400), BOX)
        times.append(sum(e.delay_before for e in events))
        hits += click_landed(events, BOX)
    return statistics.mean(times), hits / n


def signup_seconds(profile):
    user = SimulatedUser(FakeSignupApp(), profile)
    assert user.fill("Full name", "Ada Lovelace") and user.fill("Email", "ada@example.org")
    return user.report.elapsed


def test_rushing_is_faster_and_sloppier_than_taking_care():
    base = BUILTIN_PROFILES["intermediate"]
    fast, careful = emphasize(base, 1), emphasize(base, -1)
    assert fast.name == "intermediate-fast" and careful.name == "intermediate-careful"
    assert fast.typo_rate > base.typo_rate > careful.typo_rate
    assert fast.pointer_spread > careful.pointer_spread
    assert mean_click(fast)[0] < mean_click(base)[0] < mean_click(careful)[0]
    # The tremor makes the trade-off visible as missed clicks.
    motor = BUILTIN_PROFILES["motor"]
    assert mean_click(emphasize(motor, 1))[1] < mean_click(emphasize(motor, -1))[1]
    with pytest.raises(ValueError):
        emphasize(base, 2)


def test_population_is_reproducible_varied_and_valid():
    people = sample_population(50, {"expert": 1, "novice": 3}, seed=7)
    assert people == sample_population(50, {"expert": 1, "novice": 3}, seed=7)
    assert len({p.name for p in people}) == 50
    assert {p.navigation for p in people} == {"quicknav", "linear"}
    wpms = [p.typing_wpm for p in people if p.name.startswith("novice")]
    assert statistics.stdev(wpms) > 1
    assert all(0 <= p.typo_rate < 1 for p in people)


def test_speed_traits_move_together():
    people = [individual(BUILTIN_PROFILES["intermediate"], s) for s in range(300)]
    wpm = [p.typing_wpm for p in people]
    pointer = [p.pointer_b_s for p in people]
    assert statistics.correlation(wpm, pointer) < -0.3  # fast typists point fast too


def test_practice_follows_a_power_law_and_levels_off():
    novice = BUILTIN_PROFILES["novice"].with_seed(3)
    times = [signup_seconds(practiced(novice, n)) for n in (1, 2, 5, 20, 100)]
    assert times == sorted(times, reverse=True)
    assert times[0] - times[1] > times[3] - times[4]  # early visits help most
    assert practiced(novice, 1).typing_wpm == novice.typing_wpm
    assert practiced(novice, 10**6).typo_rate > novice.typo_rate * 0.49  # floor
    assert practiced(novice, 50).navigation == "linear"


def test_fatigue_slows_and_shakes():
    motor = BUILTIN_PROFILES["motor"]
    tired = fatigued(motor, 90)
    assert tired.reaction_s > motor.reaction_s and tired.pointer_tremor_px > motor.pointer_tremor_px
    assert fatigued(motor, 10_000).reaction_s == pytest.approx(motor.reaction_s * 1.6)
    assert fatigued(motor, 0).to_dict() | {"name": "", "notes": []} == motor.to_dict() | {"name": "", "notes": []}
