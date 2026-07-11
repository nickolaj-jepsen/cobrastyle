import os
from typing import Any, cast

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.templating import Jinja2Templates  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from cobrastyle.fastapi import install  # noqa: E402


def test_fastapi_dev_e2e(page_project, extract):
    app = fastapi.FastAPI()
    templates = Jinja2Templates(directory=str(page_project / "templates"))
    install(templates, app, root=page_project / "styles")

    @app.get("/")
    def index(request: fastapi.Request):
        return templates.TemplateResponse(request, "index.html")

    client = TestClient(app)

    html = client.get("/").text
    href = extract(r'href="([^"]+)"', html)
    class_name = extract(r'class="([^"]+)"', html)
    assert href == "/cobrastyle/page.css"
    assert class_name.startswith("page_title_")  # readable dev class names

    response = client.get(href)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert f".{class_name}" in response.text
    etag = response.headers["etag"]

    assert client.get(href, headers={"If-None-Match": etag}).status_code == 304
    assert client.get("/cobrastyle/missing.css").status_code == 404

    stylesheet = page_project / "styles" / "page.css"
    stylesheet.write_text(".title { color: blue; }")
    os.utime(stylesheet, (2000, 2000))

    response = client.get(href)
    assert "#00f" in response.text
    assert f".{class_name}" in response.text
    assert response.headers["etag"] != etag


def test_install_rejects_non_environment():
    untyped_install = cast(Any, install)  # the overloads make this call unwritable in typed code
    with pytest.raises(TypeError, match="Environment"):
        untyped_install(object())


def test_install_requires_resolver_root_or_manifest(page_project):
    untyped_install = cast(Any, install)
    templates = Jinja2Templates(directory=str(page_project / "templates"))

    with pytest.raises(TypeError, match="resolver=, root="):
        untyped_install(templates)


def test_install_accepts_bare_environment_and_custom_resolver(page_project):
    from jinja2 import Environment, FileSystemLoader

    from cobrastyle import FileSystemResolver

    app = fastapi.FastAPI()
    environment = Environment(loader=FileSystemLoader(page_project / "templates"))
    install(environment, app, resolver=FileSystemResolver(page_project / "styles", url_prefix="/styles/"))

    # The dev server mounts at the resolver's own prefix, not the default
    html = environment.get_template("index.html").render()
    assert "/styles/page.css" in html
    assert TestClient(app).get("/styles/page.css").status_code == 200


def test_install_serve_false_mounts_nothing(page_project):
    app = fastapi.FastAPI()
    templates = Jinja2Templates(directory=str(page_project / "templates"))
    install(templates, app, root=page_project / "styles", serve=False)

    assert TestClient(app).get("/cobrastyle/page.css").status_code == 404


def test_fastapi_serves_raw_assets(page_project):
    (page_project / "styles" / "img").mkdir()
    (page_project / "styles" / "img" / "dot.svg").write_bytes(b"<svg/>")
    app = fastapi.FastAPI()
    templates = Jinja2Templates(directory=str(page_project / "templates"))
    install(templates, app, root=page_project / "styles")

    response = TestClient(app).get("/cobrastyle/img/dot.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg")
    assert response.content == b"<svg/>"


def test_fastapi_hot_reload_endpoints(page_project):
    app = fastapi.FastAPI()
    templates = Jinja2Templates(directory=str(page_project / "templates"))
    install(templates, app, root=page_project / "styles")

    @app.get("/")
    def index(request: fastapi.Request):
        return templates.TemplateResponse(request, "index.html")

    client = TestClient(app)

    html = client.get("/").text
    assert 'src="/cobrastyle/__client__.js"' in html
    assert 'data-events="/cobrastyle/__events__"' in html
    assert client.get("/cobrastyle/__client__.js").status_code == 200
    # The event stream itself never ends, which deadlocks TestClient's in-thread
    # transport — the streaming loop is covered in test_serve.py with an explicit
    # disconnect; here the mount above proves the endpoint is reachable.


def test_fastapi_hot_reload_follows_serving(page_project):
    templates = Jinja2Templates(directory=str(page_project / "templates"))
    # No app to mount the events endpoint on: hot reload stays off
    install(templates, root=page_project / "styles")

    html = templates.env.get_template("index.html").render()
    assert "__client__.js" not in html
