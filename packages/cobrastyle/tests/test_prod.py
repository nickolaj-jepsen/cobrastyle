import re
import sys

import pytest
from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError

from cobrastyle import FileSystemResolver
from cobrastyle.build import build
from cobrastyle.jinja2 import CobrastyleExtension, configure
from cobrastyle.manifest import Manifest

URL_PREFIX = "/static/cobrastyle/"


@pytest.fixture
def built_project(tmp_path):
    templates = tmp_path / "templates"
    styles = tmp_path / "styles"
    templates.mkdir()
    styles.mkdir()

    (styles / "page.css").write_text(".title { color: red; }")
    (styles / "base.css").write_text(".layout { margin: 0; }")

    (templates / "base.html").write_text(
        '{% cobrastyle layout = "base.css" %}'
        '<head>{{ cobrastyle.links() }}</head><body class="{{ layout.layout }}">'
        "{% block content %}{% endblock %}</body>"
    )
    (templates / "index.html").write_text(
        '{% extends "base.html" %}{% block content %}{% cobrastyle styles = "page.css" %}'
        '<h1 class="{{ styles.title }}">Hi</h1>{% endblock %}'
    )

    dev_environment = Environment(loader=FileSystemLoader(templates), extensions=[CobrastyleExtension])
    configure(dev_environment, resolver=FileSystemResolver(styles))
    build(dev_environment, output_dir=tmp_path / "dist", url_prefix=URL_PREFIX)
    return tmp_path


def make_prod_environment(project) -> Environment:
    environment = Environment(loader=FileSystemLoader(project / "templates"), extensions=[CobrastyleExtension])
    configure(environment, manifest=project / "dist" / "manifest.json")
    return environment


def test_prod_render_matches_manifest(built_project):
    manifest = Manifest.load(built_project / "dist" / "manifest.json")
    environment = make_prod_environment(built_project)

    html = environment.get_template("index.html").render()

    assert manifest.modules["page.css"].classes["title"] in html
    assert manifest.modules["base.css"].classes["layout"] in html
    hrefs = re.findall(r'href="([^"]+)"', html)
    assert manifest.modules["page.css"].url in hrefs
    assert manifest.modules["base.css"].url in hrefs
    assert all(href.startswith(URL_PREFIX) for href in hrefs)


def test_prod_never_imports_compiler(built_project, monkeypatch):
    monkeypatch.delitem(sys.modules, "cobrastyle.manager", raising=False)
    # Block the Rust wheel: any import attempt now raises ImportError
    monkeypatch.setitem(sys.modules, "cobrastyle_lightningcss", None)

    environment = make_prod_environment(built_project)
    html = environment.get_template("index.html").render()

    assert "<h1" in html
    assert "cobrastyle.manager" not in sys.modules


def test_prod_missing_module_is_load_error(built_project):
    (built_project / "templates" / "new.html").write_text('{% cobrastyle styles = "brand-new.css" %}')
    environment = make_prod_environment(built_project)

    with pytest.raises(TemplateSyntaxError, match="cobrastyle build"):
        environment.get_template("new.html")


def test_prod_links_survive_worker_that_never_compiled(built_project):
    """Simulates a bytecode-cache hit: parse-time state is empty, links() falls back to the manifest."""
    environment = make_prod_environment(built_project)
    template = environment.get_template("index.html")

    extension = CobrastyleExtension.get(environment)
    assert extension is not None
    extension._pages.clear()

    manifest = Manifest.load(built_project / "dist" / "manifest.json")
    html = template.render()
    hrefs = re.findall(r'href="([^"]+)"', html)
    assert manifest.modules["page.css"].url in hrefs
    assert manifest.modules["base.css"].url in hrefs


def test_configure_requires_exactly_one_mode(built_project):
    environment = Environment(extensions=[CobrastyleExtension])

    with pytest.raises(TypeError, match="exactly one"):
        configure(environment)
    with pytest.raises(TypeError, match="exactly one"):
        configure(
            environment,
            resolver=FileSystemResolver(built_project / "styles"),
            manifest=built_project / "dist" / "manifest.json",
        )


def test_flask_prod_mode(built_project):
    flask = pytest.importorskip("flask")
    from cobrastyle.flask import Cobrastyle

    app = flask.Flask("prodapp", root_path=str(built_project))
    Cobrastyle(app, manifest=built_project / "dist" / "manifest.json")

    @app.get("/")
    def index():
        return flask.render_template("index.html")

    client = app.test_client()
    manifest = Manifest.load(built_project / "dist" / "manifest.json")

    html = client.get("/").get_data(as_text=True)
    assert manifest.modules["page.css"].url in html
    # No dev serve route in prod: built files are static
    assert client.get("/cobrastyle/page.css").status_code == 404


def test_fastapi_prod_mode(built_project):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from cobrastyle.fastapi import install

    app = fastapi.FastAPI()
    templates = Jinja2Templates(directory=str(built_project / "templates"))
    install(templates, app, manifest=built_project / "dist" / "manifest.json")

    @app.get("/")
    def index(request: fastapi.Request):
        return templates.TemplateResponse(request, "index.html")

    client = TestClient(app)
    manifest = Manifest.load(built_project / "dist" / "manifest.json")

    html = client.get("/").text
    assert manifest.modules["page.css"].url in html
    assert client.get("/cobrastyle/page.css").status_code == 404
