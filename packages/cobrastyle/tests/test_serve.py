import asyncio
import os

from cobrastyle import CobrastyleManager, FileSystemResolver, InMemoryResolver
from cobrastyle.serve import CobrastyleASGIApp, CobrastyleWSGIMiddleware, get_css, serve


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


def _asgi_request(app, method="GET", path="/test.css", headers=()):
    scope = {"type": "http", "method": method, "path": path, "root_path": "", "headers": list(headers)}
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
