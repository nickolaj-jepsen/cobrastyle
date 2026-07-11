import os

from cobrastyle import CobrastyleManager, FileSystemResolver, InMemoryResolver
from cobrastyle.serve import CobrastyleWSGIMiddleware, get_css


def test_get_css_compiles():
    manager = CobrastyleManager(InMemoryResolver({"test.css": ".a { color: red; }"}), module_pattern="[local]")

    result = get_css(manager, "test.css")

    assert result is not None
    code, etag = result
    assert code == ".a{color:red}"
    assert etag.startswith('W/"')


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


def _wsgi_get(app, path, headers=None):
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path}
    for name, value in (headers or {}).items():
        environ["HTTP_" + name.upper().replace("-", "_")] = value
    captured = {}

    def start_response(status, response_headers):
        captured["status"] = status
        captured["headers"] = dict(response_headers)

    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def test_wsgi_middleware_serves_and_falls_through():
    manager = CobrastyleManager(InMemoryResolver({"test.css": ".a { color: red; }"}), module_pattern="[local]")

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
