"""A self-contained dev server under synthetic load, for sampling profilers.

Serves a generated project through CobrastyleWSGIMiddleware on werkzeug
(threaded, like `flask run`), drives it with client threads alternating
conditional/unconditional GETs, holds SSE connections open, and touches a
stylesheet periodically so hot reload has changes to report. Exits after
--seconds; run it under py-spy:

    py-spy record -o flame.svg --subprocesses -- python benchmarks/live_server.py
"""

from __future__ import annotations

import argparse
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

import httpx
import synth
from werkzeug.serving import make_server

from cobrastyle.serve import CobrastyleWSGIMiddleware


def fallback_app(environ, start_response):
    start_response("404 Not Found", [("Content-Type", "text/plain")])
    return [b"not found"]


def client_loop(base_url: str, project: synth.Project, stop: threading.Event, counter: list[int]) -> None:
    etags: dict[str, str] = {}
    index = 0
    with httpx.Client(base_url=base_url, timeout=5.0) as client:
        while not stop.is_set():
            path = project.module_path(index)
            index += 1
            headers = {"If-None-Match": etags[path]} if index % 2 and path in etags else {}
            response = client.get(f"/cobrastyle/{path}", headers=headers)
            if etag := response.headers.get("ETag"):
                etags[path] = etag
            counter[0] += 1


def sse_loop(base_url: str, stop: threading.Event) -> None:
    with (
        httpx.Client(base_url=base_url, timeout=httpx.Timeout(5.0, read=None)) as client,
        client.stream("GET", "/cobrastyle/__events__") as response,
    ):
        for _ in response.iter_raw():
            if stop.is_set():
                return


def touch_loop(project: synth.Project, stop: threading.Event, interval: float) -> None:
    target = project.styles / project.module_path(0)
    while not stop.wait(interval):
        os.utime(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modules", type=int, default=200)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--clients", type=int, default=4)
    parser.add_argument("--sse", type=int, default=3)
    parser.add_argument("--touch-interval", type=float, default=2.0)
    args = parser.parse_args()

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    root = Path(tempfile.mkdtemp(prefix="cobrastyle-live-"))
    project = synth.generate_project(root, modules=args.modules, pages=args.modules * 2)
    manager = synth.make_manager(project)
    synth.warm(manager, project)

    server = make_server("127.0.0.1", 0, CobrastyleWSGIMiddleware(fallback_app, manager), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    stop = threading.Event()
    counter = [0]
    for _ in range(args.clients):
        threading.Thread(target=client_loop, args=(base_url, project, stop, counter), daemon=True).start()
    for _ in range(args.sse):
        threading.Thread(target=sse_loop, args=(base_url, stop), daemon=True).start()
    threading.Thread(target=touch_loop, args=(project, stop, args.touch_interval), daemon=True).start()

    time.sleep(args.seconds)
    stop.set()
    print(f"{counter[0]} requests in {args.seconds:.0f}s ({counter[0] / args.seconds:.0f} req/s)", flush=True)
    # Skip server.shutdown(): SSE handler threads never finish, and a clean
    # exit here is only cosmetic — the sampling profiler already detached.
    os._exit(0)


if __name__ == "__main__":
    main()
