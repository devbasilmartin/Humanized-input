import io
import json

import pytest

from humanized_input.bridge import Bridge, load_styles, summarize
from humanized_input.experiment import MouseStyle, Shape, evaluate, execute, grid, landed, plan

# A thin L: its bounding-box centre and its centroid are both off the shape.
L = Shape.polygon([(600, 400), (612, 400), (612, 440), (650, 440), (650, 452), (600, 452)])


def test_shapes():
    assert not L.contains(*L.box.center) and not L.contains(*L.centroid)
    x, y = L.deepest()
    assert L.contains(x, y) and L.edge_distance(x, y) >= 5
    sq = Shape.rect(0, 0, 10, 10)
    assert sq.contains(5, 5) and not sq.contains(11, 5) and sq.centroid == pytest.approx((5, 5))
    with pytest.raises(ValueError):
        Shape.polygon([(0, 0), (1, 1)])


def test_aim_strategies_on_an_irregular_shape():
    styles = grid("intermediate", path=["instant"], aim=["center", "deepest"])
    res = {r.style: r.hit_rate for r in evaluate(styles, [L], trials=50)}
    assert res["instant aim=deepest x1"] == 1.0 and res["instant aim=center x1"] == 0.0


def test_paths_and_speed():
    style = MouseStyle("s", path="linear", aim="deepest")
    events, aim = plan(style, (100, 100), L, seed=1)
    assert landed(events, L)
    moves = [e for e in events if e.kind == "move"]
    assert len(moves) > 10 and [e.kind for e in events[-2:]] == ["down", "up"]
    fast, _ = plan(MouseStyle("f", path="linear", aim="deepest", speed=2), (100, 100), L, seed=1)
    assert sum(e.delay_before for e in fast) == pytest.approx(sum(e.delay_before for e in events) / 2)
    instant, _ = plan(MouseStyle("i", path="instant", aim="deepest"), (100, 100), L, seed=1)
    assert [e.kind for e in instant] == ["move", "down", "up"]
    human, _ = plan(MouseStyle("h", path="human", aim="human"), (100, 100), Shape.rect(600, 400, 40, 40), seed=1)
    assert len(human) > 10
    with pytest.raises(ValueError):
        MouseStyle("bad", path="teleport")


def test_grid_crosses_profile_fields():
    styles = grid("expert", path=["human"], aim=["human", "deepest"], pointer_spread=[0.3, 0.9])
    assert len(styles) == 4 and len({s.name for s in styles}) == 4
    assert {s.profile.pointer_spread for s in styles} == {0.3, 0.9}


def test_execute_calls_your_functions():
    calls = []
    events, _ = plan(MouseStyle("i", path="instant", aim="center"), (0, 0), Shape.rect(10, 10, 10, 10))
    execute(events, move=lambda x, y: calls.append(("move", x, y)), down=lambda b: calls.append(("down", b)),
            up=lambda b: calls.append(("up", b)), sleep=lambda s: None)
    assert calls == [("move", 15, 15), ("down", "left"), ("up", "left")]


def test_bridge_round_trip(tmp_path):
    styles = tmp_path / "styles.yaml"
    styles.write_text("styles:\n"
                      "  - {name: precise, path: instant, aim: deepest}\n"
                      "  - {name: naive-centre, path: linear, aim: center}\n"
                      "  - {name: human, path: human, aim: human, overrides: {pointer_spread: 0.5}}\n")
    log = tmp_path / "out.jsonl"
    bridge = Bridge(load_styles(str(styles)), str(log))
    lines = []
    for i in range(6):  # round-robin: each style gets clicks i and i + 3
        lines.append({"cmd": "click", "from": [0, 0], "polygon": [list(p) for p in L.points]})
        lines.append({"cmd": "result", "id": i + 1, "ok": i != 1})
    lines += [{"cmd": "summary"}, {"cmd": "nope"}]
    out = io.StringIO()
    bridge.serve(io.StringIO("\n".join(json.dumps(m) for m in lines)), out)
    replies = [json.loads(r) for r in out.getvalue().splitlines()]
    first = replies[0]
    assert first["style"] == "precise" and first["events"][-1]["type"] == "up"
    summary = replies[-2]["summary"]
    assert len(summary) == 3 and all(r["clicks"] == 2 for r in summary)
    assert {r["style"]: r["rate"] for r in summary}["naive-centre"] == 0.5
    assert "error" in replies[-1]
    # The log carries over to the next session.
    assert len(Bridge(load_styles(str(styles)), str(log)).outcomes) == 6
    assert summarize([]) == []


def test_example_styles_file_loads():
    names = [s.name for s in load_styles("examples/mouse_styles.yaml")]
    assert "skillbot-classic" in names and len(names) == len(set(names))
