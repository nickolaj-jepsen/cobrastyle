from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseNotModified, StreamingHttpResponse
from django.http.response import HttpResponseBase
from django.template import engines
from jinja2 import Environment

from cobrastyle.jinja2 import CobrastyleExtension
from cobrastyle.serve import EVENTS_PATH, SSE_HEADERS, serve, watch_events

if TYPE_CHECKING:
    from cobrastyle.manager import CobrastyleManager


def _manager() -> CobrastyleManager | None:
    """The dev-mode manager: the Jinja2 extension's if configured, else the DTL runtime's."""
    for backend in engines.all():
        env = getattr(backend, "env", None)
        if isinstance(env, Environment) and (extension := CobrastyleExtension.get(env)) is not None:
            return extension.manager
    from cobrastyle.django.runtime import get_runtime

    return get_runtime().manager


def stylesheet(request: HttpRequest, path: str) -> HttpResponseBase:
    """Serve compiled CSS (and raw url() assets) from the cobrastyle manager. DEBUG only."""
    if not settings.DEBUG:
        raise Http404
    try:
        manager = _manager()
    except Exception:
        raise Http404 from None
    if manager is None:
        raise Http404
    if path == EVENTS_PATH and request.method == "GET":
        # A long-lived stream pins a worker thread; runserver is threaded, so
        # that's one thread per tab — fine for the DEBUG-only path this is.
        response = StreamingHttpResponse(watch_events(manager), content_type="text/event-stream")
        for name, value in SSE_HEADERS:
            if name != "Content-Type":
                response[name] = value
        return response
    result = serve(manager, path, method=request.method or "GET", if_none_match=request.headers.get("If-None-Match"))
    if result is None:
        raise Http404
    if result.status == 304:
        return HttpResponseNotModified(headers=dict(result.headers))
    return HttpResponse(result.body, status=result.status, headers=dict(result.headers))
