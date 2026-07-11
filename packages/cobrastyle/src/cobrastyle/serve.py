from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import mimetypes
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator, MutableMapping
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from wsgiref.types import StartResponse, WSGIApplication, WSGIEnvironment

    from cobrastyle.manager import CobrastyleManager, Stylesheet

# Minimal structural ASGI types; the asgiref ones are equivalent but not a dependency.
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

CONTENT_TYPE = "text/css; charset=utf-8"
# Dev responses must revalidate on every request so CSS edits show up on refresh.
CACHE_CONTROL = "no-cache"

# Reserved names under the dev serving prefix (hot reload); never valid stylesheet paths.
EVENTS_PATH = "__events__"
CLIENT_SCRIPT_PATH = "__client__.js"

EVENTS_POLL_INTERVAL = 0.3
# Comment frames on an idle stream, so buffering proxies don't close the connection.
EVENTS_HEARTBEAT = 15.0

SSE_HEADERS = [
    ("Content-Type", "text/event-stream"),
    ("Cache-Control", "no-cache"),
    ("X-Accel-Buffering", "no"),
]

_RESOLVE_ERRORS = (KeyError, ValueError, FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError)


class Resource(NamedTuple):
    body: bytes
    content_type: str
    etag: str


class Served(NamedTuple):
    status: int
    headers: list[tuple[str, str]]
    body: bytes


# On a change event, swap in a cache-busted clone of each matching <link> and drop
# the old one once the clone has loaded, so the page never renders unstyled.
CLIENT_JS = """\
(() => {
  const source = new EventSource(document.currentScript.dataset.events);
  source.addEventListener("change", (event) => {
    const { url } = JSON.parse(event.data);
    for (const link of document.querySelectorAll('link[rel="stylesheet"]')) {
      const href = link.getAttribute("href");
      if (href === null || href.split("?")[0] !== url) continue;
      const fresh = link.cloneNode();
      fresh.href = url + "?t=" + Date.now();
      fresh.onload = () => link.remove();
      fresh.onerror = () => fresh.remove();
      link.after(fresh);
    }
  });
})();
"""

_CLIENT_RESOURCE = Resource(
    CLIENT_JS.encode(),
    "text/javascript; charset=utf-8",
    f'W/"{hashlib.sha256(CLIENT_JS.encode()).hexdigest()[:16]}"',
)


def hot_reload_script_html(url_prefix: str) -> str:
    """The dev-only script tag loading the hot-reload client from the serving prefix."""
    prefix = html.escape(url_prefix if url_prefix.endswith("/") else url_prefix + "/", quote=True)
    return f'<script src="{prefix}{CLIENT_SCRIPT_PATH}" data-events="{prefix}{EVENTS_PATH}" defer></script>'


def _freshness_token(manager: CobrastyleManager, stylesheet: Stylesheet) -> tuple[object, ...]:
    tokens: list[object] = []
    for dep, _ in stylesheet.dep_mtimes:
        try:
            tokens.append(manager.resolver.mtime(dep))
        except (KeyError, OSError):
            tokens.append("missing")
    return tuple(tokens)


def poll_changes(manager: CobrastyleManager, seen: dict[str, tuple[object, ...]]) -> list[Stylesheet]:
    """Return cached stylesheets whose source files changed since the last call with ``seen``.

    ``seen`` is the caller's (per-connection) freshness state; stylesheets it
    doesn't know yet are primed silently — a module compiled mid-connection is
    new, not changed.
    """
    changed = []
    for stylesheet in manager.stylesheets:
        token = _freshness_token(manager, stylesheet)
        previous = seen.get(stylesheet.path)
        seen[stylesheet.path] = token
        if previous is not None and previous != token:
            changed.append(stylesheet)
    return changed


def event_frames(manager: CobrastyleManager, seen: dict[str, tuple[object, ...]]) -> list[bytes]:
    """SSE ``change`` frames for every stylesheet :func:`poll_changes` reports."""
    return [
        f"event: change\ndata: {json.dumps({'path': s.path, 'url': s.url})}\n\n".encode()
        for s in poll_changes(manager, seen)
    ]


def watch_events(
    manager: CobrastyleManager,
    *,
    interval: float = EVENTS_POLL_INTERVAL,
    heartbeat: float = EVENTS_HEARTBEAT,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[bytes]:
    """SSE frames for the hot-reload events endpoint: an opening comment (flushes
    headers through buffering servers), then ``change`` events as stylesheet
    sources change, with keep-alive comments while idle. Never returns; the
    consumer streams it until the client disconnects."""
    seen: dict[str, tuple[object, ...]] = {}
    poll_changes(manager, seen)
    yield b": cobrastyle\n\n"
    idle = 0.0
    while True:
        sleep(interval)
        frames = event_frames(manager, seen)
        if frames:
            yield from frames
            idle = 0.0
        else:
            idle += interval
            if idle >= heartbeat:
                yield b": keep-alive\n\n"
                idle = 0.0


def serve(
    manager: CobrastyleManager,
    path: str,
    *,
    method: str = "GET",
    if_none_match: str | None = None,
) -> Served | None:
    """Resolve ``path`` into a complete conditional-GET response; None means not found.

    Owns the caching contract for every framework adapter: weak-ETag
    comparison, the 200/304 header sets, and empty bodies for HEAD.
    """
    if method not in ("GET", "HEAD"):
        return None
    resource = _CLIENT_RESOURCE if path == CLIENT_SCRIPT_PATH else get_resource(manager, path)
    if resource is None:
        return None
    if _etag_matches(if_none_match, resource.etag):
        return Served(304, [("ETag", resource.etag)], b"")
    headers = [
        ("Content-Type", resource.content_type),
        ("Content-Length", str(len(resource.body))),
        ("ETag", resource.etag),
        ("Cache-Control", CACHE_CONTROL),
    ]
    return Served(200, headers, resource.body if method == "GET" else b"")


def get_css(manager: CobrastyleManager, path: str) -> tuple[str, str] | None:
    """Compile (or revalidate) the module at resolver-relative ``path``.

    Returns ``(code, weak_etag)``, or None when the path is not a resolvable
    ``.css`` file.
    """
    if not path.endswith(".css"):
        return None
    try:
        stylesheet = manager.import_module(path)
    except _RESOLVE_ERRORS:
        return None
    code = stylesheet.code
    if stylesheet.map is not None:
        encoded = base64.b64encode(stylesheet.map.encode()).decode()
        code = f"{code}\n/*# sourceMappingURL=data:application/json;base64,{encoded} */"
    return code, _etag(stylesheet)


def get_resource(manager: CobrastyleManager, path: str) -> Resource | None:
    """Serve ``path`` from the manager's resolver: compiled CSS for ``.css``, raw bytes otherwise.

    Raw serving keeps ``url()`` references (icons, fonts) working in dev,
    where compiled CSS is served relative to the resolver root.
    """
    if path.endswith(".css"):
        result = get_css(manager, path)
        if result is None:
            return None
        code, etag = result
        return Resource(code.encode(), CONTENT_TYPE, etag)

    try:
        data = manager.resolver.read_bytes(path)
    except _RESOLVE_ERRORS:
        return None
    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    digest = hashlib.sha256(data).hexdigest()[:16]
    return Resource(data, content_type, f'W/"{digest}"')


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110 If-None-Match: a comma-separated list or ``*``, weak-compared (``W/`` ignored)."""
    if if_none_match is None:
        return False
    if if_none_match.strip() == "*":
        return True
    opaque = etag.removeprefix("W/")
    return any(candidate.strip().removeprefix("W/") == opaque for candidate in if_none_match.split(","))


def _etag(stylesheet: Stylesheet) -> str:
    if stylesheet.mtime is not None:
        return f'W/"{stylesheet.mtime}-{len(stylesheet.code)}"'
    digest = hashlib.sha256(stylesheet.code.encode()).hexdigest()[:16]
    return f'W/"{digest}"'


class CobrastyleWSGIMiddleware:
    """WSGI middleware serving compiled CSS under ``url_prefix``; everything else falls through."""

    def __init__(self, app: WSGIApplication, manager: CobrastyleManager, url_prefix: str = "/static/"):
        self.app = app
        self.manager = manager
        self.url_prefix = url_prefix

    def __call__(self, environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET")
        if method == "GET" and path == self.url_prefix + EVENTS_PATH:
            # A long-lived streaming response pins a worker thread; the default dev
            # servers (werkzeug, runserver) are threaded, so that's one thread per tab.
            start_response("200 OK", list(SSE_HEADERS))
            return watch_events(self.manager)
        if method in ("GET", "HEAD") and path.startswith(self.url_prefix):
            result = serve(
                self.manager,
                path[len(self.url_prefix) :],
                method=method,
                if_none_match=environ.get("HTTP_IF_NONE_MATCH"),
            )
            if result is not None:
                start_response(f"{result.status} {HTTPStatus(result.status).phrase}", result.headers)
                return [result.body]
        return self.app(environ, start_response)


class CobrastyleASGIApp:
    """ASGI app serving compiled CSS. Mount it at the resolver's ``url_prefix``."""

    def __init__(self, manager: CobrastyleManager):
        self.manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            raise RuntimeError("CobrastyleASGIApp only handles http scopes")
        # This app owns its mount point outright, so a wrong method is 405, not 404.
        if scope["method"] not in ("GET", "HEAD"):
            headers = [(b"content-type", b"text/plain"), (b"allow", b"GET, HEAD")]
            await _respond(send, 405, headers, b"Method Not Allowed")
            return
        # Mounted apps receive the full path with root_path set to the mount point.
        path = scope["path"]
        root_path = scope.get("root_path", "")
        if root_path and path.startswith(root_path):
            path = path[len(root_path) :]
        if scope["method"] == "GET" and path.lstrip("/") == EVENTS_PATH:
            await self._serve_events(receive, send)
            return
        request_headers = dict(scope.get("headers", []))
        result = serve(
            self.manager,
            path.lstrip("/"),
            method=scope["method"],
            if_none_match=request_headers.get(b"if-none-match", b"").decode() or None,
        )
        if result is None:
            await _respond(send, 404, [(b"content-type", b"text/plain")], b"Not Found")
            return
        headers = [(name.lower().encode(), value.encode()) for name, value in result.headers]
        await _respond(send, result.status, headers, result.body)

    async def _serve_events(self, receive: Receive, send: Send) -> None:
        headers = [(name.lower().encode(), value.encode()) for name, value in SSE_HEADERS]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        seen: dict[str, tuple[object, ...]] = {}
        poll_changes(self.manager, seen)
        await send({"type": "http.response.body", "body": b": cobrastyle\n\n", "more_body": True})
        disconnected = asyncio.ensure_future(_wait_for_disconnect(receive))
        idle = 0.0
        try:
            while True:
                done, _ = await asyncio.wait({disconnected}, timeout=EVENTS_POLL_INTERVAL)
                if disconnected in done:
                    return
                frames = event_frames(self.manager, seen)
                if not frames:
                    idle += EVENTS_POLL_INTERVAL
                    if idle >= EVENTS_HEARTBEAT:
                        frames = [b": keep-alive\n\n"]
                if frames:
                    idle = 0.0
                    for frame in frames:
                        await send({"type": "http.response.body", "body": frame, "more_body": True})
        finally:
            disconnected.cancel()


async def _wait_for_disconnect(receive: Receive) -> None:
    while (await receive())["type"] != "http.disconnect":
        pass


async def _respond(send: Send, status: int, headers: list[tuple[bytes, bytes]], body: bytes) -> None:
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
