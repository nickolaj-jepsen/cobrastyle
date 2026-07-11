from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
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

_RESOLVE_ERRORS = (KeyError, ValueError, FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError)


class Resource(NamedTuple):
    body: bytes
    content_type: str
    etag: str


class Served(NamedTuple):
    status: int
    headers: list[tuple[str, str]]
    body: bytes


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
    resource = get_resource(manager, path)
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
    return stylesheet.code, _etag(stylesheet)


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


async def _respond(send: Send, status: int, headers: list[tuple[bytes, bytes]], body: bytes) -> None:
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
