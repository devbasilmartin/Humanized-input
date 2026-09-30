"""Tests for the human-like mouse model. All but the last run without a browser."""

import math
from pathlib import Path

import pytest

from humanized_input import BUILTIN_PROFILES, SimulatedUser
from humanized_input.ax import AXItem
from humanized_input.backends import Backend
from humanized_input.backends.win_input import normalise_point
from humanized_input.pointer import PointerPlanner, Rect, click_landed, min_jerk, pointer_path
from humanized_input.profiles import Profile
from humanized_input.timing import Humanizer


def planner(name="intermediate", seed=0):
    return PointerPlanner(Humanizer(BUILTIN_PROFILES[name].with_seed(seed)))


def hit_rate(name, box, n=400):
    return sum(click_landed(planner(name, s).plan_click((10, 10), box), box) for s in range(n)) / n


def test_min_jerk_starts_and_ends_at_rest():
    assert min_jerk(0) == 0 and min_jerk(1) == 1 and min_jerk(0.5) == pytest.approx(0.5)
    assert min_jerk(0.01) < 0.001  # slow start, like a real hand


def test_fitts_law_harder_targets_take_longer():
    p = planner()
    assert p.fitts_time(800, 20) > p.fitts_time(200, 20) > p.fitts_time(200, 80)


@pytest.mark.parametrize("name", ["expert", "intermediate", "novice"])
@pytest.mark.parametrize("seed", range(20))
def test_move_ends_inside_the_target(name, seed):
    box = Rect(500, 300, 30, 20)
    events, _ = planner(name, seed).plan_move((20, 700), box)
    assert all(e.delay_before >= 0 for e in events)
    assert box.contains(events[-1].x, events[-1].y)
    # Every step is a small, pixel-sized mouse report, never a teleport.
    path = [(20, 700)] + pointer_path(events)
    assert max(math.dist(a, b) for a, b in zip(path, path[1:])) < 120


def test_speed_peaks_mid_movement():
    events, _ = planner("expert", 3).plan_move((0, 0), Rect(900, 0, 40, 40))
    speeds = [math.dist((a.x, a.y), (b.x, b.y)) / b.delay_before for a, b in zip(events, events[1:])]
    peak = speeds.index(max(speeds))
    assert 0.15 * len(speeds) < peak < 0.85 * len(speeds)
    assert speeds[0] < max(speeds) / 3


def test_paths_bow_instead_of_running_straight():
    offsets = []
    for seed in range(20):
        events, _ = planner("novice", seed).plan_move((0, 0), Rect(1000, -10, 20, 20))
        offsets.append(max(abs(e.y) for e in events))
    assert sum(offsets) / len(offsets) > 10


def test_same_seed_same_path():
    box = Rect(100, 100, 50, 50)
    assert planner("motor", 9).plan_click((0, 0), box) == planner("motor", 9).plan_click((0, 0), box)


def test_double_click_has_two_presses():
    events = planner("expert", 1).plan_click((0, 0), Rect(50, 50, 40, 40), clicks=2)
    assert [e.kind for e in events if e.kind != "move"] == ["down", "up", "down", "up"]


def test_tremor_misses_small_targets_more_than_large_ones():
    small, large = Rect(600, 400, 12, 12), Rect(600, 400, 48, 48)
    assert hit_rate("expert", small) == 1.0  # no tremor: always on target
    assert hit_rate("motor", small) < hit_rate("motor", large)
    assert hit_rate("motor", small) < 0.95


def test_pointer_profile_validation():
    with pytest.raises(ValueError):
        Profile(name="bad", pointer_b_s=0)
    with pytest.raises(ValueError):
        Profile(name="bad", pointer_tremor_px=-1)


def test_sendinput_coordinates_span_the_virtual_desktop():
    desktop = (-1920, 0, 3840, 1080)  # a monitor left of the primary one
    assert normalise_point(-1920, 0, desktop) == (0, 0)
    assert normalise_point(1919, 1079, desktop) == (65535, 65535)
    assert normalise_point(5000, -50, desktop) == (65535, 0)  # clamped to the screen


# --- a simulated user clicking in a fake app ----------------------------------


class FakeMouseApp(Backend):
    def __init__(self, button_box):
        self.box = button_box
        self.pos = (0.0, 0.0)
        self.pressed_on = None
        self.clicks = 0

    def snapshot(self):
        return "App", [AXItem("button", "Save", backend_id=1), AXItem("button", "Ghost", backend_id=2)]

    def focused(self):
        return None

    def bounds(self, item):
        return self.box if item.backend_id == 1 else None

    def mouse_position(self):
        return self.pos

    def mouse_move(self, x, y):
        self.pos = (x, y)

    def mouse_button(self, button, down):
        inside = self.box.contains(*self.pos)
        if down:
            self.pressed_on = inside
        elif self.pressed_on and inside:
            self.clicks += 1  # like a real toolkit: press and release on the button


def test_user_clicks_a_button():
    app = FakeMouseApp(Rect(300, 200, 90, 32))
    user = SimulatedUser(app, BUILTIN_PROFILES["novice"].with_seed(2))
    assert user.click("Save")
    assert app.clicks == 1
    assert user.report.goals[-1].seconds > 0.5


def test_click_reports_why_it_failed():
    user = SimulatedUser(FakeMouseApp(Rect(0, 0, 10, 10)), BUILTIN_PROFILES["expert"])
    assert not user.click("Ghost")
    assert user.report.goals[-1].note == "has no on-screen box to click"
    assert not user.click("Nothing here")
    assert user.report.goals[-1].note == "not on screen"


def test_motor_user_sometimes_misses_a_tiny_button():
    results, notes = [], set()
    for seed in range(40):
        app = FakeMouseApp(Rect(300, 200, 10, 10))
        user = SimulatedUser(app, BUILTIN_PROFILES["motor"].with_seed(seed))
        results.append(user.click("Save"))
        notes.add(user.report.goals[-1].note)
        assert app.clicks == int(results[-1])  # the report agrees with the app
    assert 0 < sum(results) < len(results)
    assert notes == {"", "missed the target (10x10 px)"}


# --- real Chromium ------------------------------------------------------------------


def test_mouse_click_in_the_browser():
    from humanized_input.session import user_session

    page = (Path(__file__).parent.parent / "examples" / "signup.html").resolve().as_uri()
    try:
        session = user_session(page, BUILTIN_PROFILES["expert"].with_seed(4))
        user = session.__enter__()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"browser unavailable: {exc}")
    try:
        page_obj = user.backend.page
        page_obj.evaluate("window.moves = 0; addEventListener('mousemove', () => moves++)")
        assert user.click("agree to the terms", kind="field")
        assert page_obj.is_checked("input[type=checkbox]")
        assert page_obj.evaluate("moves") > 10  # a path of reports, not a jump
    finally:
        session.__exit__(None, None, None)
