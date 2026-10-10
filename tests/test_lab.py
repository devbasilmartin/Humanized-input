import math

from humanized_input.experiment import Shape
from humanized_input.lab import analyze, changes, load_lab

RING = [[0, 0], [60, 0], [60, 60], [0, 60], [0, 0], [12, 12], [12, 48], [48, 48], [48, 12], [12, 12]]


def _doc(task, i, **kw):
    return {"session": "s", "task": task, "trial": i, **kw}


def _recording():
    docs = []
    dx, dy = Shape.polygon(RING).deepest()
    for i in range(6):  # always clicks the ring's band near its deepest point, never the hole in the middle
        docs.append(_doc("shapes", i, polygon=RING, start=[300, 300], moves=[[100, dx, dy]],
                         down=[400, dx + 1, dy], up=[500, dx + 1, dy], hit=True))
    for i in range(6):  # presses on the centre where the box is at the press: leading
        v, t = (120 if i % 2 else -120), 0.8
        x0 = 100.0
        docs.append(_doc("moving", i, target=[x0, 200, 30, 40], velocity=[v, 0], start=[0, 0], moves=[],
                         down=[t * 1000, x0 + v * t + 15, 220], up=[t * 1000 + 90, x0 + v * t + 15, 220], hit=True))
    for i in range(5):
        docs.append(_doc("reaction", i, target=[300, 300, 24, 24], start=[0, 0],
                         moves=[[100, 0, 1], [240, 0, 5], [400, 100, 100]], down=[600, 310, 310], up=[700, 310, 310]))
    docs.append(_doc("hold", 0, circle=[50, 50, 20], start=[50, 50],
                     moves=[[600 + 10 * k, 50 + 2 * math.sin(k * 3.1), 50] for k in range(200)]))
    return docs


def test_lab_analysis_suggests_settings():
    report = analyze(_recording())
    assert report["shapes"]["aim"] == "deepest"
    assert report["moving"]["lead"] is True and abs(report["moving"]["lag"]) < 0.05
    assert report["reaction"]["reaction_s"] == 0.24
    assert 1.0 < report["hold"]["tremor_px"] < 3.0
    assert set(changes(report)) == {"aim", "lead", "reaction_s", "pointer_tremor_px"}


def test_lagging_clicks_mean_no_lead():
    docs = [_doc("moving", i, target=[100, 200, 30, 40], velocity=[120, 0], start=[0, 0], moves=[],
                 down=[800, 115, 220], up=[890, 115, 220], hit=False) for i in range(4)]
    assert analyze(docs)["moving"]["lead"] is False


def test_load_lab_groups_by_task():
    assert set(load_lab({"trials": _recording()})) == {"shapes", "moving", "reaction", "hold"}
