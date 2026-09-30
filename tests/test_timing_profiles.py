import random
import statistics

import pytest

from humanized_input.ax import AXItem, flatten
from humanized_input.profiles import BUILTIN_PROFILES, Profile, load_profile
from humanized_input.screen_reader import describe
from humanized_input.timing import Humanizer, apply_plan


@pytest.mark.parametrize("name", list(BUILTIN_PROFILES))
@pytest.mark.parametrize("seed", range(20))
def test_typing_plan_always_produces_the_text(name, seed):
    profile = BUILTIN_PROFILES[name].with_seed(seed)
    text = "Ada Lovelace <ada@example.org> 1815"
    plan = Humanizer(profile).plan_typing(text)
    assert apply_plan("", plan) == text
    assert all(k.delay_before >= 0 for k in plan)


def test_typos_happen_and_get_corrected():
    profile = Profile(name="sloppy", typo_rate=0.3, seed=1)
    plan = Humanizer(profile).plan_typing("the quick brown fox jumps")
    assert any(k.is_correction for k in plan)
    assert apply_plan("", plan) == "the quick brown fox jumps"


def test_mean_key_interval_matches_wpm():
    profile = Profile(name="p", typing_wpm=60, seed=3)
    h = Humanizer(profile)
    samples = [h.key_interval("a") for _ in range(20000)]
    expected = 60 / (60 * 5)  # 0.2 s per character
    assert statistics.mean(samples) == pytest.approx(expected, rel=0.05)
    # Right-skewed like real typing: the mean is above the median.
    assert statistics.mean(samples) > statistics.median(samples)


def test_skimming_listens_to_fewer_words():
    expert = Humanizer(BUILTIN_PROFILES["expert"], random.Random(0))
    text = "Library card number, edit, required, blank"
    assert expert.listen_time(text) < expert.listen_time(text, full=True)


def test_profile_validation_and_file_loading(tmp_path):
    with pytest.raises(ValueError):
        Profile(name="bad", navigation="teleport")
    path = tmp_path / "p.json"
    path.write_text('{"name": "custom", "typing_wpm": 33, "think_s": [0.1, 0.2]}')
    p = load_profile(str(path))
    assert p.typing_wpm == 33 and p.think_s == (0.1, 0.2)
    assert load_profile("profiles/example.yaml").name == "one-handed-typist"


def test_describe_matches_screen_reader_phrasing():
    assert describe(AXItem("heading", "Welcome", level=2)) == "Welcome, heading, level 2"
    assert describe(AXItem("textbox", "Email", states={"required": True})) == "Email, edit, required, blank"
    assert describe(AXItem("checkbox", "Agree")) == "Agree, check box, not checked"
    assert describe(AXItem("button", "")) == "button"


def _node(i, role, name="", parent=None, children=(), ignored=False, **props):
    n = {"nodeId": str(i), "role": {"value": role}, "name": {"value": name},
         "childIds": [str(c) for c in children], "ignored": ignored,
         "properties": [{"name": k, "value": {"value": v}} for k, v in props.items()]}
    if parent is not None:
        n["parentId"] = str(parent)
    return n


def test_flatten_reading_order_skips_ignored_wrappers():
    nodes = [
        _node(1, "RootWebArea", "Title", children=[2]),
        _node(2, "generic", parent=1, children=[3, 5], ignored=True),
        _node(3, "heading", "Hello", parent=2, children=[4], level=1),
        _node(4, "StaticText", "Hello", parent=3),
        _node(5, "button", "Go", parent=2),
    ]
    title, items = flatten(nodes)
    assert title == "Title"
    assert [(i.role, i.name) for i in items] == [("heading", "Hello"), ("button", "Go")]
