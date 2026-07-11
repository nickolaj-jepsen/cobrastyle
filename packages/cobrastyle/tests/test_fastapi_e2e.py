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
