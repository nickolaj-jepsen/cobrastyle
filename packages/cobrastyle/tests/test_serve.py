import asyncio
import logging
import os

import pytest

from cobrastyle import CobrastyleManager, FileSystemResolver, InMemoryResolver
from cobrastyle.serve import CobrastyleASGIApp, CobrastyleWSGIMiddleware, error_css, get_css, get_resource, serve


def test_get_css_compiles():
    manager = CobrastyleManager(
        InMemoryResolver({"test.css": ".a { color: red; }"}), module_pattern="[local]", minify=True, source_map=False
    )

    result = get_css(manager, "test.css")

    assert result is not None
    code, etag = result
    assert code == ".a{color:red}"
    assert etag.startswith('W/"')


def test_get_css_appends_inline_source_map():
    manager = CobrastyleManager(InMemoryResolver({"test.css": ".a { color: red; }"}), module_pattern="[local]")

    result = get_css(manager, "test.css")

    assert result is not None
    code, _ = result
    assert "/*# sourceMappingURL=data:application/json;base64," in code


def test_get_css_rejects_non_css_and_missing():
    manager = CobrastyleManager(InMemoryResolver({"test.css": ".a {}", "notes.txt": "hi"}))

    assert get_css(manager, "notes.txt") is None
    assert get_css(manager, "missing.css") is None
    assert get_css(manager, "../escape.css") is None


def test_compile_error_is_served_as_an_overlay_not_a_404(caplog):
    manager = CobrastyleManager(InMemoryResolver({"broken.css": "..a { color: red; }"}))

    with caplog.at_level(logging.ERROR, logger="cobrastyle.serve"):
        result = serve(manager, "broken.css")

    assert result is not None
    assert result.status == 200
    body = result.body.decode()
    assert "failed to compile broken.css" in body
    assert "html::before" in body
    assert "failed to compile 'broken.css'" in caplog.text

    head = serve(manager, "broken.css", method="HEAD")
    assert head is not None
    assert head.status == 200
    assert head.body == b""


def test_missing_import_dependency_surfaces_as_compile_error():
    manager = CobrastyleManager(InMemoryResolver({"entry.css": '@import "missing.css";'}))

    result = serve(manager, "entry.css")

    assert result is not None
    assert result.status == 200
    assert "failed to compile entry.css" in result.body.decode()


def test_error_css_escapes_the_message():
    css = error_css("x.css", 'a "quote" and a */ comment close')

    # The message's */ must not terminate the comment early; quotes must not end the content string.
    assert "*\\/ comment close" in css.split("*/", 1)[0]
    assert '\\"quote\\"' in css


def test_etag_changes_with_content(tmp_path):
    css = tmp_path / "test.css"
    css.write_text(".a { color: red; }")
    os.utime(css, (1000, 1000))
    manager = CobrastyleManager(FileSystemResolver(tmp_path))

    first = get_css(manager, "test.css")
    css.write_text(".a { color: blue; }")
    os.utime(css, (2000, 2000))
    second = get_css(manager, "test.css")

    assert first is not None
    assert second is not None
    assert first[1] != second[1]


def test_serve_ignores_other_methods():
    manager = CobrastyleManager(InMemoryResolver({"test.css": ".a { color: red; }"}))

    assert serve(manager, "test.css", method="POST") is None


def test_etag_hashes_content_when_the_resolver_has_no_mtime():
    def etag_for(css):
        manager = CobrastyleManager(InMemoryResolver({"test.css": css}), source_map=False)
        result = serve(manager, "test.css")
        assert result is not None
        return dict(result.headers)["ETag"]

    red = etag_for(".a { color: red; }")
    assert red.startswith('W/"')
    assert etag_for(".a { color: red; }") == red
    assert etag_for(".a { color: blue; }") != red


def test_raw_resource_unknown_extension_is_octet_stream():
    manager = CobrastyleManager(InMemoryResolver({"font.xyzzy": "abc"}))

    resource = get_resource(manager, "font.xyzzy")

    assert resource is not None
    assert resource.content_type == "application/octet-stream"


def test_serve_head_has_headers_but_no_body():
    manager = CobrastyleManager(
        InMemoryResolver({"test.css": ".a { color: red; }"}), module_pattern="[local]", minify=True, source_map=False
    )

    result = serve(manager, "test.css", method="HEAD")

    assert result is not None
    assert result.status == 200
    assert result.body == b""
    headers = dict(result.headers)
    assert headers["Content-Length"] == str(len(b".a{color:red}"))
    assert headers["ETag"]


def test_serve_if_none_match_forms():
    manager = CobrastyleManager(
        InMemoryResolver({"test.css": ".a { color: red; }"}), module_pattern="[local]", minify=True, source_map=False
    )
    first = serve(manager, "test.css")
    assert first is not None
    etag = dict(first.headers)["ETag"]
    opaque = etag.removeprefix("W/")

    def status(if_none_match):
        result = serve(manager, "test.css", if_none_match=if_none_match)
        assert result is not None
        return result.status

    assert status(None) == 200
    assert status('"unrelated"') == 200
    assert status(etag) == 304
    assert status(f'"unrelated", {etag}') == 304
    assert status(opaque) == 304  # strong form still matches: comparison is weak
    assert status("*") == 304


def _wsgi_get(app, path, headers=None, method="GET"):
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path}
    for name, value in (headers or {}).items():
        environ["HTTP_" + name.upper().replace("-", "_")] = value
    captured = {}

    def start_response(status, response_headers):
        captured["status"] = status
        captured["headers"] = dict(response_headers)

    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def test_wsgi_middleware_serves_and_falls_through():
    manager = CobrastyleManager(
        InMemoryResolver({"test.css": ".a { color: red; }"}), module_pattern="[local]", minify=True, source_map=False
    )

    def fallback(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"fallback"]

    app = CobrastyleWSGIMiddleware(fallback, manager, url_prefix="/static/")

    status, headers, body = _wsgi_get(app, "/static/test.css")
    assert status == "200 OK"
    assert headers["Content-Type"].startswith("text/css")
    assert body == b".a{color:red}"

    status, _, _ = _wsgi_get(app, "/static/test.css", headers={"If-None-Match": headers["ETag"]})
    assert status == "304 Not Modified"

    status, _, body = _wsgi_get(app, "/other")
    assert body == b"fallback"
    status, _, body = _wsgi_get(app, "/static/missing.css")
    assert body == b"fallback"
    status, _, body = _wsgi_get(app, "/static/test.css", method="POST")
    assert body == b"fallback"


def test_wsgi_middleware_serves_raw_assets():
    manager = CobrastyleManager(InMemoryResolver({"img/icon.svg": "<svg></svg>"}))

    def fallback(environ, start_response):
        start_response("404 Not Found", [])
        return [b""]

    app = CobrastyleWSGIMiddleware(fallback, manager, url_prefix="/static/")

    status, headers, body = _wsgi_get(app, "/static/img/icon.svg")
    assert status == "200 OK"
    assert headers["Content-Type"] == "image/svg+xml"
    assert body == b"<svg></svg>"


def test_wsgi_middleware_never_serves_outside_the_resolver_root(tmp_path):
    (tmp_path / "styles").mkdir()
    (tmp_path / "styles" / "page.css").write_text(".a { color: red; }")
    (tmp_path / "secret.txt").write_text("s3cr3t")
    manager = CobrastyleManager(FileSystemResolver(tmp_path / "styles"))

    def fallback(environ, start_response):
        start_response("404 Not Found", [])
        return [b"fallthrough"]

    app = CobrastyleWSGIMiddleware(fallback, manager, url_prefix="/static/")

    for path in ("/static/../secret.txt", "/static/../secret.css", "/static//secret.txt"):
        _, _, body = _wsgi_get(app, path)
        assert b"s3cr3t" not in body


def _asgi_request(app, method="GET", path="/test.css", headers=(), root_path=""):
    scope = {"type": "http", "method": method, "path": path, "root_path": root_path, "headers": list(headers)}
    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start = sent[0]
    body = b"".join(message.get("body", b"") for message in sent[1:])
    return start["status"], dict(start["headers"]), body


def test_asgi_app_serves_css():
    manager = CobrastyleManager(
        InMemoryResolver({"test.css": ".a { color: red; }"}), module_pattern="[local]", minify=True, source_map=False
    )
    app = CobrastyleASGIApp(manager)

    status, headers, body = _asgi_request(app)
    assert status == 200
    assert body == b".a{color:red}"

    status, _, _ = _asgi_request(app, headers=[(b"if-none-match", headers[b"etag"])])
    assert status == 304

    status, _, _ = _asgi_request(app, path="/missing.css")
    assert status == 404


def test_asgi_app_rejects_other_methods():
    manager = CobrastyleManager(InMemoryResolver({"test.css": ".a {}"}))
    app = CobrastyleASGIApp(manager)

    status, headers, _ = _asgi_request(app, method="POST")
    assert status == 405
    assert headers[b"allow"] == b"GET, HEAD"


def test_asgi_app_serves_raw_assets():
    manager = CobrastyleManager(InMemoryResolver({"icon.svg": "<svg></svg>"}))
    app = CobrastyleASGIApp(manager)

    status, headers, body = _asgi_request(app, path="/icon.svg")
    assert status == 200
    assert headers[b"content-type"] == b"image/svg+xml"
    assert body == b"<svg></svg>"


def test_asgi_app_strips_the_mount_root_path():
    manager = CobrastyleManager(
        InMemoryResolver({"test.css": ".a { color: red; }"}), module_pattern="[local]", minify=True, source_map=False
    )
    app = CobrastyleASGIApp(manager)

    status, _, body = _asgi_request(app, path="/cobrastyle/test.css", root_path="/cobrastyle")
    assert status == 200
    assert body == b".a{color:red}"


def test_asgi_app_rejects_non_http_scopes():
    app = CobrastyleASGIApp(CobrastyleManager(InMemoryResolver({})))

    async def receive():
        return {}

    async def send(message):
        pass

    with pytest.raises(RuntimeError, match="http"):
        asyncio.run(app({"type": "websocket"}, receive, send))


def test_client_script_is_served():
    from cobrastyle.serve import CLIENT_SCRIPT_PATH

    manager = CobrastyleManager(InMemoryResolver({}))

    result = serve(manager, CLIENT_SCRIPT_PATH)

    assert result is not None
    assert result.status == 200
    headers = dict(result.headers)
    assert headers["Content-Type"].startswith("text/javascript")
    assert b"EventSource" in result.body

    revalidated = serve(manager, CLIENT_SCRIPT_PATH, if_none_match=headers["ETag"])
    assert revalidated is not None
    assert revalidated.status == 304


def test_poll_changes_reports_an_edit_once(tmp_path):
    from cobrastyle.serve import poll_changes

    css = tmp_path / "test.css"
    css.write_text(".a { color: red; }")
    os.utime(css, (1000, 1000))
    manager = CobrastyleManager(FileSystemResolver(tmp_path))
    manager.import_module("test.css")

    seen = {}
    assert poll_changes(manager, seen) == []  # first poll primes silently

    css.write_text(".a { color: blue; }")
    os.utime(css, (2000, 2000))
    assert [stylesheet.path for stylesheet in poll_changes(manager, seen)] == ["test.css"]
    assert poll_changes(manager, seen) == []


def test_poll_changes_covers_imported_files(tmp_path):
    from cobrastyle.serve import poll_changes

    (tmp_path / "theme.css").write_text(":root { --x: red; }")
    (tmp_path / "page.css").write_text('@import "./theme.css";\n.a { color: red; }')
    for name in ("theme.css", "page.css"):
        os.utime(tmp_path / name, (1000, 1000))
    manager = CobrastyleManager(FileSystemResolver(tmp_path))
    manager.import_module("page.css")

    seen = {}
    poll_changes(manager, seen)
    os.utime(tmp_path / "theme.css", (2000, 2000))

    assert [stylesheet.path for stylesheet in poll_changes(manager, seen)] == ["page.css"]


def test_watch_events_stream(tmp_path):
    import json

    from cobrastyle.serve import watch_events

    css = tmp_path / "test.css"
    css.write_text(".a { color: red; }")
    os.utime(css, (1000, 1000))
    manager = CobrastyleManager(FileSystemResolver(tmp_path, url_prefix="/styles/"))
    manager.import_module("test.css")

    frames = watch_events(manager, sleep=lambda _: None)
    assert next(frames) == b": cobrastyle\n\n"

    css.write_text(".a { color: blue; }")
    os.utime(css, (2000, 2000))
    frame = next(frames)
    assert frame.startswith(b"event: change\ndata: ")
    assert frame.endswith(b"\n\n")
    payload = json.loads(frame.removeprefix(b"event: change\ndata: "))
    assert payload == {"path": "test.css", "url": "/styles/test.css"}


def test_watch_events_heartbeat_while_idle():
    from cobrastyle.serve import watch_events

    manager = CobrastyleManager(InMemoryResolver({"a.css": ".a { color: red; }"}))
    manager.import_module("a.css")

    frames = watch_events(manager, interval=1.0, heartbeat=2.0, sleep=lambda _: None)
    assert next(frames) == b": cobrastyle\n\n"
    assert next(frames) == b": keep-alive\n\n"


def test_wsgi_middleware_streams_events():
    from cobrastyle.serve import EVENTS_PATH

    manager = CobrastyleManager(InMemoryResolver({"test.css": ".a { color: red; }"}))

    def fallback(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"fallback"]

    app = CobrastyleWSGIMiddleware(fallback, manager, url_prefix="/static/")
    captured = {}

    def start_response(status, response_headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = dict(response_headers)
        return lambda _: None  # the WSGI write() callable, unused

    body = app({"REQUEST_METHOD": "GET", "PATH_INFO": f"/static/{EVENTS_PATH}"}, start_response)
    assert captured["status"] == "200 OK"
    assert captured["headers"]["Content-Type"] == "text/event-stream"
    assert next(iter(body)) == b": cobrastyle\n\n"

    # Anything but a plain GET falls through to the app
    _, _, body = _wsgi_get(app, f"/static/{EVENTS_PATH}", method="POST")
    assert body == b"fallback"


def test_asgi_app_streams_events_until_disconnect():
    manager = CobrastyleManager(InMemoryResolver({"test.css": ".a { color: red; }"}))
    app = CobrastyleASGIApp(manager)
    scope = {"type": "http", "method": "GET", "path": "/__events__", "root_path": "", "headers": []}
    messages = []
    received = iter([{"type": "http.disconnect"}])

    async def receive():
        return next(received)

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))

    start = messages[0]
    assert start["status"] == 200
    assert dict(start["headers"])[b"content-type"] == b"text/event-stream"
    assert messages[1] == {"type": "http.response.body", "body": b": cobrastyle\n\n", "more_body": True}
