import json
import threading
import urllib.error
import urllib.request

import pytest

from humanized_input.styles import PARAMS, STARTER, StyleLibrary
from humanized_input.ui import serve


@pytest.fixture()
def server(tmp_path):
    srv = serve(tmp_path, port=0, open_browser=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", StyleLibrary(tmp_path)
    srv.shutdown()


def call(base, method, path, body=None, headers=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}") if "json" in r.headers["Content-Type"] else r.read()
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_page_and_metadata(server):
    base, _ = server
    code, html = call(base, "GET", "/")
    assert code == 200 and b"Mouse Style Panel" in html
    code, meta = call(base, "GET", "/api/meta")
    assert [p["key"] for p in meta["params"]] == [p[0] for p in PARAMS] and "L shape" in meta["targets"]


def test_save_rename_preview_compare_delete(server):
    base, lib = server
    assert call(base, "POST", "/api/starter")[1]["added"] == [s.name for s in STARTER]
    styles = call(base, "GET", "/api/styles")[1]["styles"]
    human = next(s for s in styles if s["name"] == "human")
    human["profile"]["pointer_spread"] = 0.2
    human["name"] = "tight"
    assert call(base, "POST", "/api/styles", {"style": human, "previous_name": "human"})[0] == 200
    assert "human" not in lib.names() and lib.load("tight").profile.pointer_spread == 0.2
    # Renaming onto an existing style is refused.
    assert call(base, "POST", "/api/styles", {"style": {**human, "name": "precise"}, "previous_name": "tight"})[0] == 409
    code, prev = call(base, "POST", "/api/preview", {"style": human, "target": "walking 120 px/s", "samples": 8})
    assert code == 200 and len(prev["paths"]) == 8 and prev["velocity"] == [120.0, 0.0]
    assert all(p["points"][0][2] == 0 for p in prev["paths"])
    code, res = call(base, "POST", "/api/compare", {"styles": [human], "trials": 20})
    assert code == 200 and res["results"][0]["style"] == "tight"
    assert call(base, "DELETE", "/api/styles/tight")[0] == 200 and "tight" not in lib.names()


def test_bad_input_and_cross_origin(server):
    base, _ = server
    assert call(base, "POST", "/api/preview", {"style": {"name": "x", "path": "warp"}})[0] == 400
    assert call(base, "GET", "/api/styles", headers={"Origin": "https://evil.example"})[0] == 403
    assert call(base, "GET", "/nope")[0] == 404
