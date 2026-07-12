"""Adapter overhead: what the WSGI middleware costs per request, including the
passthrough tax every non-cobrastyle request in the app pays."""

from __future__ import annotations

import synth
from pytest_benchmark.fixture import BenchmarkFixture

from cobrastyle.manager import CobrastyleManager
from cobrastyle.serve import CobrastyleWSGIMiddleware


def _fallback_app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"ok"]


def _environ(path: str, **extra: str) -> dict[str, str]:
    return {"PATH_INFO": path, "REQUEST_METHOD": "GET", **extra}


def _start_response(status, headers):
    return None


def test_wsgi_passthrough(warm_manager: CobrastyleManager, benchmark: BenchmarkFixture) -> None:
    middleware = CobrastyleWSGIMiddleware(_fallback_app, warm_manager)
    environ = _environ("/app/dashboard")

    benchmark(middleware, environ, _start_response)


def test_wsgi_css_hit(warm_manager: CobrastyleManager, project: synth.Project, benchmark: BenchmarkFixture) -> None:
    middleware = CobrastyleWSGIMiddleware(_fallback_app, warm_manager)
    environ = _environ("/cobrastyle/" + project.module_path(3))

    benchmark(middleware, environ, _start_response)
