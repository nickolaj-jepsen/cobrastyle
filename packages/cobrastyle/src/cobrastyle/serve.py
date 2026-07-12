from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import posixpath
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator, MutableMapping
from typing import TYPE_CHECKING, Any, NamedTuple

from cobrastyle.errors import CobrastyleError, StylesheetNotFoundError, StylesheetPathError
from cobrastyle.markup import CLIENT_SCRIPT_PATH, EVENTS_PATH, hot_reload_script_html, stylesheet_links_html
from cobrastyle.paths import normalize_path
from cobrastyle.paths import url_prefix as normalize_url_prefix

__all__ = [
    "CLIENT_SCRIPT_PATH",
    "EVENTS_PATH",
    "SSE_HEADERS",
    "CobrastyleASGIApp",
    "CobrastyleWSGIMiddleware",
    "Served",
    "error_css",
    "hot_reload_script_html",
    "serve",
    "stylesheet_links_html",
    "watch_events",
]

if TYPE_CHECKING:
    from wsgiref.types import StartResponse, WSGIApplication, WSGIEnvironment

    from cobrastyle.manager import CobrastyleManager, Stylesheet

logger = logging.getLogger(__name__)

# Minimal structural ASGI types; the asgiref ones are equivalent but not a dependency.
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

CONTENT_TYPE = "text/css; charset=utf-8"
# Dev responses must revalidate on every request so CSS edits show up on refresh.
CACHE_CONTROL = "no-cache"

# Raw-asset content types by suffix, memoized (see _content_type)
_CONTENT_TYPES: dict[str, str] = {}

# serve() only ever answers 200 or 304; http.HTTPStatus costs ~0.5ms to import and
# serve.py is on the prod import path through the adapters.
_STATUS_LINES = {200: "200 OK", 304: "304 Not Modified"}

EVENTS_POLL_INTERVAL = 0.3
# Comment frames on an idle stream, so buffering proxies don't close the connection.
EVENTS_HEARTBEAT = 15.0

SSE_HEADERS = [
    ("Content-Type", "text/event-stream"),
    ("Cache-Control", "no-cache"),
    ("X-Accel-Buffering", "no"),
]

# Raw-asset misses that mean 404. Deliberately no plain ValueError: the compiler's
# TransformError subclasses it, and compile failures must surface, not vanish.
_RESOLVE_ERRORS = (
    KeyError,
    FileNotFoundError,
    IsADirectoryError,
    NotADirectoryError,
    PermissionError,
    StylesheetPathError,
)


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


# Not None: that's a real token (resolvers without freshness), and "missing" marks a vanished file.
_UNPOLLED = object()


def _freshness_token(
    manager: CobrastyleManager, stylesheet: Stylesheet, mtimes: dict[str, object]
) -> tuple[object, ...]:
    tokens: list[object] = []
    for dep, _ in stylesheet.dep_mtimes:
        token = mtimes.get(dep, _UNPOLLED)
        if token is _UNPOLLED:
            try:
                token = manager.resolver.mtime(dep)
            except (KeyError, OSError):
                token = "missing"
            mtimes[dep] = token
        tokens.append(token)
    return tuple(tokens)


def poll_changes(manager: CobrastyleManager, seen: dict[str, tuple[object, ...]]) -> list[Stylesheet]:
    """Return cached stylesheets whose source files changed since the last call with ``seen``.

    ``seen`` is the caller's (per-connection) freshness state; stylesheets it
    doesn't know yet are primed silently — a module compiled mid-connection is
    new, not changed.
    """
    changed = []
    # Dependencies shared across modules (a common reset) stat once per poll, not per stylesheet
    mtimes: dict[str, object] = {}
    for stylesheet in manager.stylesheets:
        token = _freshness_token(manager, stylesheet, mtimes)
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


def _tick(
    manager: CobrastyleManager,
    seen: dict[str, tuple[object, ...]],
    idle: float,
    interval: float,
    heartbeat: float,
) -> tuple[list[bytes], float]:
    """One poll of the events stream: (frames to emit, new idle time)."""
    frames = event_frames(manager, seen)
    if frames:
        return frames, 0.0
    idle += interval
    if idle >= heartbeat:
        return [b": keep-alive\n\n"], 0.0
    return [], idle


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
        frames, idle = _tick(manager, seen, idle, interval, heartbeat)
        yield from frames


def serve(
    manager: CobrastyleManager,
    path: str,
    *,
    method: str = "GET",
    if_none_match: str | None = None,
) -> Served | None:
    """Resolve ``path`` into a complete conditional-GET response; None means not found.

    Owns the caching contract for every framework adapter: weak-ETag
    comparison, the 200/304 header sets, and empty bodies for HEAD. A module
    that fails to *compile* is not a 404: the failure is logged and served as
    a stylesheet that overlays the error on the page (see :func:`error_css`).
    """
    if method not in ("GET", "HEAD"):
        return None
    try:
        negotiated = _negotiate(manager, path)
        if negotiated is None:
            return None
        if _etag_matches(if_none_match, negotiated.etag):
            return Served(304, [("ETag", negotiated.etag)], b"")
        try:
            body = negotiated.body()
        except _RESOLVE_ERRORS:
            # A directory, unreadable file, or a delete racing the stat: still a miss
            return None
    except Exception as exc:
        if not _is_compile_error(exc):
            raise
        logger.error("cobrastyle failed to compile %r: %s", path, exc)
        body = error_css(path, exc).encode()
        headers = [
            ("Content-Type", CONTENT_TYPE),
            ("Content-Length", str(len(body))),
            ("Cache-Control", CACHE_CONTROL),
        ]
        # 200 without an ETag: browsers only apply 2xx stylesheets, and the next
        # request must revalidate so the fix (or a new error) always shows.
        return Served(200, headers, body if method == "GET" else b"")
    headers = [
        ("Content-Type", negotiated.content_type),
        ("Content-Length", str(len(body))),
        ("ETag", negotiated.etag),
        ("Cache-Control", CACHE_CONTROL),
    ]
    return Served(200, headers, body if method == "GET" else b"")


class Negotiated(NamedTuple):
    """A resolved resource whose ETag is known before its body is built.

    ``body`` runs only on an ETag miss, so 304 revalidations of raw assets
    skip the file read entirely (compiled CSS bodies are prebuilt anyway).
    """

    etag: str
    content_type: str
    body: Callable[[], bytes]


def _negotiate(manager: CobrastyleManager, path: str) -> Negotiated | None:
    if path == CLIENT_SCRIPT_PATH:
        resource = _CLIENT_RESOURCE
        return Negotiated(resource.etag, resource.content_type, lambda: resource.body)
    if path.endswith(".css"):
        return _negotiate_css(manager, path)
    return _negotiate_raw(manager, path)


def _negotiate_css(manager: CobrastyleManager, path: str) -> Negotiated | None:
    try:
        path = normalize_path(path)
    except StylesheetPathError:
        return None
    try:
        stylesheet = manager.import_module(path)
    except StylesheetNotFoundError as exc:
        if exc.path == path:
            return None
        raise  # a missing @import or composes dependency is a compile failure, not a 404
    return Negotiated(stylesheet.etag, CONTENT_TYPE, lambda: stylesheet.body)


def _content_type(path: str) -> str:
    # guess_type re-parses the whole path; a dev project reuses a handful of suffixes
    suffix = posixpath.splitext(path)[1]
    content_type = _CONTENT_TYPES.get(suffix)
    if content_type is None:
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        _CONTENT_TYPES[suffix] = content_type
    return content_type


def _negotiate_raw(manager: CobrastyleManager, path: str) -> Negotiated | None:
    content_type = _content_type(path)
    try:
        mtime = manager.resolver.mtime(path)
        if mtime is None:
            # No freshness token (in-memory sources): the ETag must hash the content
            data = manager.resolver.read_bytes(path)
            digest = hashlib.sha256(data).hexdigest()[:16]
            return Negotiated(f'W/"{digest}"', content_type, lambda: data)
    except _RESOLVE_ERRORS:
        return None
    return Negotiated(f'W/"{mtime}"', content_type, lambda: manager.resolver.read_bytes(path))


def get_css(manager: CobrastyleManager, path: str) -> tuple[str, str] | None:
    """Compile (or revalidate) the module at resolver-relative ``path``.

    Returns ``(code, weak_etag)``, or None when the path is not a resolvable
    ``.css`` file. Compile failures (bad CSS, broken composes chains, missing
    dependencies) propagate — they are errors to surface, not 404s.
    """
    if not path.endswith(".css"):
        return None
    negotiated = _negotiate_css(manager, path)
    if negotiated is None:
        return None
    return negotiated.body().decode(), negotiated.etag


def get_resource(manager: CobrastyleManager, path: str) -> Resource | None:
    """Serve ``path`` from the manager's resolver: compiled CSS for ``.css``, raw bytes otherwise.

    Raw serving keeps ``url()`` references (icons, fonts) working in dev,
    where compiled CSS is served relative to the resolver root.
    """
    negotiated = _negotiate_css(manager, path) if path.endswith(".css") else _negotiate_raw(manager, path)
    if negotiated is None:
        return None
    try:
        return Resource(negotiated.body(), negotiated.content_type, negotiated.etag)
    except _RESOLVE_ERRORS:
        return None


def _is_compile_error(exc: Exception) -> bool:
    # Local import: serve.py is on the prod import path (via the adapters), the compiler isn't.
    from cobrastyle_lightningcss import TransformError

    return isinstance(exc, (TransformError, CobrastyleError))


def error_css(path: str, error: object) -> str:
    """A dev-only stylesheet that overlays a compile failure on the page.

    Served in place of a module that failed to compile, so the developer sees
    the error where they are looking — the page — instead of a silent 404 in
    the network tab.
    """
    message = f"cobrastyle: failed to compile {path}\n\n{error}"
    content = message.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\A ")
    return (
        f"/* {message.replace('*/', '*\\/')} */\n"
        "html::before {\n"
        f'  content: "{content}";\n'
        "  position: fixed;\n"
        "  top: 0;\n"
        "  left: 0;\n"
        "  right: 0;\n"
        "  z-index: 2147483647;\n"
        "  padding: 12px;\n"
        "  background: #b00020;\n"
        "  color: #fff;\n"
        "  font: 14px/1.4 monospace;\n"
        "  white-space: pre-wrap;\n"
        "}\n"
    )


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110 If-None-Match: a comma-separated list or ``*``, weak-compared (``W/`` ignored)."""
    if if_none_match is None:
        return False
    if if_none_match.strip() == "*":
        return True
    opaque = etag.removeprefix("W/")
    return any(candidate.strip().removeprefix("W/") == opaque for candidate in if_none_match.split(","))


class CobrastyleWSGIMiddleware:
    """WSGI middleware serving compiled CSS under ``url_prefix``; everything else falls through."""

    def __init__(self, app: WSGIApplication, manager: CobrastyleManager, url_prefix: str = "/cobrastyle/"):
        self.app = app
        self.manager = manager
        self.url_prefix = normalize_url_prefix(url_prefix)
        self._events_path = self.url_prefix + EVENTS_PATH

    def __call__(self, environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET")
        if method == "GET" and path == self._events_path:
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
                start_response(_STATUS_LINES[result.status], result.headers)
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
        # Local import: asyncio costs ~17ms and WSGI-only apps import this module too
        import asyncio

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
                frames, idle = _tick(self.manager, seen, idle, EVENTS_POLL_INTERVAL, EVENTS_HEARTBEAT)
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
