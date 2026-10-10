"""A local control panel for mouse styles.

    python -m humanized_input.ui                 # edits ./styles, opens http://127.0.0.1:8765
    python -m humanized_input.ui --dir my_styles --port 9000 --no-browser

Edit any saved style with sliders and see sample paths drawn live on test
targets, with their hit rate and timing; save, duplicate, delete, and compare
styles in simulation. It reads and writes the same folder as
`python -m humanized_input.styles` and the bridge. Only this computer can
connect (it listens on 127.0.0.1). Standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import unquote

from .experiment import MouseStyle, Shape, evaluate, landed, plan
from .styles import COMPARE_TARGETS, PARAMS, STARTER, StyleLibrary

# Preview targets, centred in a 900 x 560 canvas.
TARGETS = {
    "square 24px": (Shape.rect(438, 268, 24, 24), None),
    "square 12px": (Shape.rect(444, 274, 12, 12), None),
    "L shape": (Shape.polygon([(430, 260), (442, 260), (442, 300), (480, 300), (480, 312), (430, 312)]), None),
    "ring": (Shape.polygon([(420, 250), (480, 250), (480, 310), (420, 310), (420, 250),
                            (432, 262), (432, 298), (468, 298), (468, 262), (432, 262)]), None),
    "walking 120 px/s": (Shape.rect(380, 260, 30, 40), (120.0, 0.0)),
}
CANVAS = (900, 560)


def _style_from_json(data: dict) -> MouseStyle:
    return MouseStyle.from_dict(data)


def preview(style: MouseStyle, target: str, samples: int = 24, seed: int = 0) -> dict:
    shape, velocity = TARGETS[target]
    rng = random.Random(seed)
    cx, cy = shape.box.center
    paths, hits, times = [], 0, []
    for i in range(samples):
        a, d = rng.uniform(0, 2 * math.pi), rng.uniform(180, 260)
        start = (min(max(cx + d * math.cos(a), 10), CANVAS[0] - 10), min(max(cy + d * math.sin(a), 10), CANVAS[1] - 10))
        events, aim = plan(style, start, shape, seed * 10_007 + i, velocity=velocity)
        ok = landed(events, shape, velocity)
        hits += ok
        t, pts, presses = 0.0, [[round(start[0]), round(start[1]), 0.0]], []
        for e in events:
            t += e.delay_before
            if e.kind == "move":
                pts.append([e.x, e.y, round(t, 4)])
            else:
                presses.append([e.kind, e.x, e.y, round(t, 4)])
        times.append(t)
        paths.append({"points": pts, "presses": presses, "aim": [round(aim[0], 1), round(aim[1], 1)], "ok": ok})
    times.sort()
    return {"shape": [list(p) for p in shape.points], "velocity": velocity, "paths": paths,
            "hit_rate": hits / samples, "mean_s": sum(times) / len(times), "p90_s": times[int(0.9 * (len(times) - 1))]}


class Handler(BaseHTTPRequestHandler):
    lib: StyleLibrary = StyleLibrary()

    def log_message(self, *args):  # keep the console quiet
        pass

    def _send(self, code: int, body, ctype: str = "application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _route(self, method: str) -> None:
        # Only same-origin requests: refuse pages on other sites poking a local port.
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            return self._send(403, {"error": "cross-origin request refused"})
        path = unquote(self.path.split("?")[0])
        try:
            if method == "GET" and path == "/":
                html = resources.files("humanized_input").joinpath("ui.html").read_bytes()
                return self._send(200, html, "text/html; charset=utf-8")
            if method == "GET" and path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if method == "GET" and path == "/api/meta":
                return self._send(200, {"params": [dict(zip(("key", "group", "kind", "spec", "help"), p))
                                                   for p in PARAMS],
                                        "targets": list(TARGETS), "canvas": CANVAS, "folder": str(self.lib.folder)})
            if method == "GET" and path == "/api/styles":
                return self._send(200, {"styles": [self.lib.load(n).to_dict() for n in self.lib.names()]})
            if method == "POST" and path == "/api/styles":
                body = self._body()
                style = _style_from_json(body["style"])
                old = body.get("previous_name")
                if old and old != style.name and style.name in self.lib.names():
                    return self._send(409, {"error": f"a style named {style.name!r} already exists"})
                self.lib.save(style, overwrite=True)
                if old and old != style.name and old in self.lib.names():
                    self.lib.delete(old)  # renamed
                return self._send(200, {"saved": style.name})
            if method == "POST" and path == "/api/starter":
                have = set(self.lib.names())
                added = [s.name for s in STARTER if s.name not in have]
                for s in STARTER:
                    if s.name in added:
                        self.lib.save(s)
                return self._send(200, {"added": added})
            if method == "DELETE" and path.startswith("/api/styles/"):
                self.lib.delete(path.removeprefix("/api/styles/"))
                return self._send(200, {"deleted": True})
            if method == "POST" and path == "/api/preview":
                body = self._body()
                return self._send(200, preview(_style_from_json(body["style"]), body.get("target", "L shape"),
                                               int(body.get("samples", 24)), int(body.get("seed", 0))))
            if method == "POST" and path == "/api/compare":
                body = self._body()
                styles = [_style_from_json(s) for s in body["styles"]]
                res = evaluate(styles, COMPARE_TARGETS, trials=int(body.get("trials", 200)))
                return self._send(200, {"results": [r.__dict__ for r in res]})
            return self._send(404, {"error": "not found"})
        except (ValueError, KeyError, TypeError, FileExistsError) as exc:
            return self._send(400, {"error": str(exc)})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")


def serve(folder: str | Path = "styles", port: int = 8765, open_browser: bool = True) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"lib": StyleLibrary(folder)})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Mouse style panel: {url}  (styles folder: {Path(folder).resolve()}; Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    return server


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Local control panel for mouse styles.")
    ap.add_argument("--dir", default="styles", help="the style folder (default: ./styles)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    server = serve(args.dir, args.port, not args.no_browser)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
