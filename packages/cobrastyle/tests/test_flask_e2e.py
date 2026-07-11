import os

import pytest

flask = pytest.importorskip("flask")

from cobrastyle.flask import Cobrastyle  # noqa: E402


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
