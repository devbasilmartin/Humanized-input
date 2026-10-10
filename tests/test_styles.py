import io
import json
from dataclasses import replace

import pytest

from humanized_input.bridge import Bridge, load_styles
from humanized_input.experiment import MouseStyle, Shape, evaluate, landed, plan
from humanized_input.profiles import BUILTIN_PROFILES, load_profile, save_profile
from humanized_input.styles import STARTER, StyleLibrary, apply_changes, main

SQUARE = Shape.rect(500, 300, 30, 40)
WALKING = (120.0, 0.0)


def test_ease_path_moves_like_pyautogui():
    # pyautogui steps at most every 50 ms, so a 0.2 s move is 4 jumps.
    events, _ = plan(MouseStyle("e", path="ease", aim="center", ease_s=(0.2, 0.2)), (0, 0), SQUARE)
    moves = [e for e in events if e.kind == "move"]
    assert len(moves) == 4 and all(e.delay_before == pytest.approx(0.05) for e in moves)
    assert [e.kind for e in events[-2:]] == ["down", "up"] and events[-1].delay_before == 0


def test_middle_aim_stays_in_the_middle_half():
    for seed in range(50):
        _, (x, y) = plan(MouseStyle("m", path="instant", aim="middle"), (0, 0), SQUARE, seed)
        assert 507.5 <= x <= 522.5 and 310 <= y <= 330


def test_clicks_and_button():
    for path in ("human", "linear", "instant", "ease"):
        events, _ = plan(MouseStyle("d", path=path, aim="deepest", clicks=2, button="right"), (0, 0), SQUARE)
        presses = [e for e in events if e.kind != "move"]
        assert [e.kind for e in presses] == ["down", "up", "down", "up"], path
        assert {e.button for e in presses} == {"right"}
    with pytest.raises(ValueError):
        MouseStyle("bad", button="side")


@pytest.mark.parametrize("path", ["human", "linear", "instant"])
def test_leading_hits_a_moving_target(path):
    lead, plain = (MouseStyle("x", path=path, aim="deepest", lead=lead) for lead in (True, False))
    hits = lambda st: sum(landed(plan(st, (100, 100), SQUARE, s, velocity=WALKING)[0], SQUARE, WALKING)  # noqa: E731
                          for s in range(30))
    assert hits(lead) == 30
    if path != "instant":  # an instant click is too quick to miss a walking target
        assert hits(plain) < 5


def test_style_round_trips_through_a_dict():
    style = MouseStyle("s", profile=replace(BUILTIN_PROFILES["motor"], pointer_spread=0.42), path="ease",
                       aim="middle", speed=1.5, lead=True, clicks=2, button="right", ease_s=(0.1, 0.2),
                       description="d")
    assert MouseStyle.from_dict(style.to_dict()) == style


def test_library_save_load_edit_delete(tmp_path):
    lib = StyleLibrary(tmp_path)
    lib.save(STARTER[0])
    with pytest.raises(FileExistsError):
        lib.save(STARTER[0])
    edited = apply_changes(lib.load("skillbot-classic"), {"speed": 2, "pointer_spread": 0.3, "ease_s": [0.1, 0.1]})
    assert edited.speed == 2 and edited.profile.pointer_spread == 0.3 and edited.ease_s == (0.1, 0.1)
    with pytest.raises(ValueError):
        apply_changes(edited, {"warp": 9})
    lib.save(edited, overwrite=True)
    assert lib.load("skillbot-classic") == edited and lib.names() == ["skillbot-classic"]
    lib.delete("skillbot-classic")
    assert lib.names() == []
    with pytest.raises(KeyError):
        lib.load("skillbot-classic")


def test_command_line(tmp_path, capsys):
    d = str(tmp_path / "styles")
    prof = save_profile(replace(BUILTIN_PROFILES["expert"], name="me", pointer_spread=0.25), tmp_path / "me.yaml")
    assert load_profile(str(prof)).pointer_spread == 0.25
    main(["--dir", d, "starter"])
    main(["--dir", d, "new", "careful", "--from", "precise", "--set", "path=linear", "--set", "lead=true",
          "--description", "slow"])
    main(["--dir", d, "new", "me", "--profile", str(prof), "--set", "aim=deepest"])
    main(["--dir", d, "edit", "me", "--set", "pointer_flick_s=0.2"])
    lib = StyleLibrary(d)
    assert lib.load("careful").path == "linear" and lib.load("careful").lead
    assert lib.load("me").profile.pointer_spread == 0.25 and lib.load("me").profile.pointer_flick_s == 0.2
    capsys.readouterr()
    main(["--dir", d, "list"])
    assert "careful" in capsys.readouterr().out
    main(["--dir", d, "compare", "precise", "careful", "--trials", "20"])
    assert "careful" in capsys.readouterr().out


def test_bridge_takes_a_folder_and_a_velocity(tmp_path):
    lib = StyleLibrary(tmp_path)
    for style in STARTER:
        lib.save(style)
    bridge = Bridge(load_styles(str(tmp_path)))
    out = io.StringIO()
    msgs = [{"cmd": "click", "from": [0, 0], "rect": [500, 300, 30, 40], "velocity": [120, 0], "style": "human-lead"},
            {"cmd": "click", "from": [0, 0], "rect": [500, 300, 30, 40], "style": "precise", "button": "right"}]
    bridge.serve(io.StringIO("\n".join(json.dumps(m) for m in msgs)), out)
    lead, right = (json.loads(r) for r in out.getvalue().splitlines())
    assert lead["aim"][0] > 530  # aimed ahead of the walking target
    assert {e["button"] for e in right["events"] if e["type"] != "move"} == {"right"}


def test_evaluate_accepts_moving_targets():
    styles = [MouseStyle("lead", path="human", aim="deepest", lead=True), MouseStyle("plain", path="human", aim="deepest")]
    res = {r.style: r.hit_rate for r in evaluate(styles, [(SQUARE, WALKING)], trials=40)}
    assert res["lead"] > 0.9 > 0.2 > res["plain"]
