import os

import pytest

flask = pytest.importorskip("flask")

from cobrastyle import InMemoryResolver  # noqa: E402
from cobrastyle.flask import Cobrastyle, init_app  # noqa: E402


def test_flask_dev_e2e(page_project, extract):
    app = flask.Flask("testapp", root_path=str(page_project))
    Cobrastyle(app)

    @app.get("/")
    def index():
        return flask.render_template("index.html")

    client = app.test_client()

    html = client.get("/").get_data(as_text=True)
    href = extract(r'href="([^"]+)"', html)
    class_name = extract(r'class="([^"]+)"', html)
    assert href == "/cobrastyle/page.css"
    assert class_name.startswith("page_title_")  # readable dev class names

    response = client.get(href)
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/css")
    css = response.get_data(as_text=True)
    assert f".{class_name}" in css
    etag = response.headers["ETag"]

    assert client.get(href, headers={"If-None-Match": etag}).status_code == 304
    assert client.get("/cobrastyle/missing.css").status_code == 404

    # Edit the stylesheet: a plain refresh serves fresh CSS, class names survive
    stylesheet = page_project / "styles" / "page.css"
    stylesheet.write_text(".title { color: blue; }")
    os.utime(stylesheet, (2000, 2000))

    response = client.get(href)
    fresh = response.get_data(as_text=True)
    assert "#00f" in fresh
    assert f".{class_name}" in fresh
    assert response.headers["ETag"] != etag


def test_flask_deferred_init_and_raw_assets(page_project):
    (page_project / "styles" / "img").mkdir()
    (page_project / "styles" / "img" / "dot.svg").write_bytes(b"<svg/>")
    app = flask.Flask("testapp", root_path=str(page_project))
    extension = Cobrastyle()
    extension.init_app(app)
    client = app.test_client()

    response = client.get("/cobrastyle/img/dot.svg")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("image/svg")
    assert response.get_data() == b"<svg/>"

    head = client.head("/cobrastyle/page.css")
    assert head.status_code == 200
    assert head.get_data() == b""
    assert app.extensions["cobrastyle"] is extension


def test_flask_serve_false_registers_no_route(page_project):
    app = flask.Flask("testapp", root_path=str(page_project))
    Cobrastyle(app, serve=False)

    @app.get("/")
    def index():
        return flask.render_template("index.html")

    client = app.test_client()
    assert "/cobrastyle/page.css" in client.get("/").get_data(as_text=True)
    assert client.get("/cobrastyle/page.css").status_code == 404


def test_flask_resolver_without_url_prefix_falls_back_to_the_argument(page_project):
    app = flask.Flask("testapp", root_path=str(page_project))
    resolver = InMemoryResolver({"page.css": ".title { color: red; }"})
    Cobrastyle(app, resolver=resolver, url_prefix="/styles/")

    response = app.test_client().get("/styles/page.css")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/css")


def test_init_app_shorthand(page_project):
    app = flask.Flask("testapp", root_path=str(page_project))
    extension = init_app(app)

    assert app.extensions["cobrastyle"] is extension
    assert app.test_client().get("/cobrastyle/page.css").status_code == 200


def test_flask_hot_reload_endpoints(page_project):
    app = flask.Flask("testapp", root_path=str(page_project))
    Cobrastyle(app)

    @app.get("/")
    def index():
        return flask.render_template("index.html")

    client = app.test_client()

    html = client.get("/").get_data(as_text=True)
    assert 'src="/cobrastyle/__client__.js"' in html
    assert 'data-events="/cobrastyle/__events__"' in html
    assert client.get("/cobrastyle/__client__.js").status_code == 200

    response = client.get("/cobrastyle/__events__", buffered=False)
    assert response.headers["Content-Type"] == "text/event-stream"
    assert next(response.response) == b": cobrastyle\n\n"
    response.close()


def test_flask_hot_reload_opt_out(page_project):
    app = flask.Flask("testapp", root_path=str(page_project))
    Cobrastyle(app, hot_reload=False)

    @app.get("/")
    def index():
        return flask.render_template("index.html")

    html = app.test_client().get("/").get_data(as_text=True)
    assert "__client__.js" not in html
