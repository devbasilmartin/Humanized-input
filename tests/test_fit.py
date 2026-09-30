import json
import math
from dataclasses import replace

import pytest

from humanized_input import BUILTIN_PROFILES
from humanized_input.fit import FITTED, Trial, compare, fit_profile, load_recording, measure, simulate
from humanized_input.pointer import Rect


def ring_geometry(widths=(12, 24, 48), distances=(150, 300, 500), n=9):
    """The recorder's layout: rings of squares, clicked alternately across."""
    trials = []
    for w in widths:
        for d in distances:
            pts = [(600 + d / 2 * math.cos(2 * math.pi * k / n), 400 + d / 2 * math.sin(2 * math.pi * k / n))
                   for k in range(n)]
            order = [(k * 5) % n for k in range(n + 1)]
            for a, b in zip(order, order[1:]):
                x, y = pts[b]
                trials.append(Trial(pts[a], Rect(x - w / 2, y - w / 2, w, w), (), (0, 0, 0), (0, 0, 0)))
    return trials


HIDDEN = replace(BUILTIN_PROFILES["novice"], pointer_a_s=0.2, pointer_b_s=0.14, pointer_spread=0.8,
                 pointer_curvature=0.05, pointer_tremor_px=1.5, pointer_settle_s=0.2, pointer_hold_s=0.13)


def test_measures_respond_to_the_profile():
    geom = ring_geometry()
    slow = measure(simulate(replace(HIDDEN, pointer_b_s=0.3), geom, repeats=1))
    fast = measure(simulate(replace(HIDDEN, pointer_b_s=0.1), geom, repeats=1))
    assert slow["fitts_b_s_per_bit"] > fast["fitts_b_s_per_bit"]
    steady = measure(simulate(replace(HIDDEN, pointer_tremor_px=0), geom, repeats=1))
    assert steady["hold_drift_px"] == 0 and measure(simulate(HIDDEN, geom, repeats=1))["hold_drift_px"] > 1


def test_fit_recovers_a_hidden_profile():
    recording = simulate(HIDDEN, ring_geometry(), repeats=3, seed=999)
    fitted, want, got = fit_profile(recording, "intermediate", repeats=1)
    for key in FITTED:
        assert got[key] == pytest.approx(want[key], rel=0.1, abs=0.02), key
    for fld in ("pointer_spread", "pointer_curvature", "pointer_settle_s", "pointer_hold_s", "pointer_tremor_px"):
        assert getattr(fitted, fld) == pytest.approx(getattr(HIDDEN, fld), rel=0.2), fld
    # Non-pointer traits come from the base profile.
    assert fitted.typing_wpm == BUILTIN_PROFILES["intermediate"].typing_wpm and fitted.name == "me"
    assert "fitted" in compare(want, got)


def test_load_recording_accepts_blocks_or_per_click_documents(tmp_path):
    trial = {"start": [0, 0], "target": [100, 0, 20, 20], "moves": [[5, 3, 0], [300, 110, 10]],
             "down": [400, 110, 10], "up": [500, 111, 10]}
    path = tmp_path / "rec.json"
    path.write_text(json.dumps({"blocks": [{"block": 0, "trials": [trial, trial]}]}))
    assert len(load_recording(path)) == 2
    docs = [{"session": "s", "block": 0, "trial": i, **trial} for i in (1, 0)]
    t = load_recording(docs)[0]
    assert t.box == Rect(100, 0, 20, 20) and t.down == (0.4, 110.0, 10.0) and t.hit
    with pytest.raises(ValueError):
        load_recording({"blocks": []})
