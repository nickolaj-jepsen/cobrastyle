"""Smoke tests keeping every example runnable: dev render + CSS serving, then build + prod boot."""

import importlib
import importlib.util
import re
import sys

import pytest

from cobrastyle.build import build
from cobrastyle.manifest import Manifest


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def css_hrefs(html: str) -> list[str]:
    return re.findall(r'href="([^"]+\.css)"', html)


def test_flask_example(copy_example, monkeypatch):
    pytest.importorskip("flask")
    monkeypatch.delenv("COBRASTYLE_ENV", raising=False)
    project = copy_example("flask")
    module = load_module(project / "app.py", "cobrastyle_example_flask")

    client = module.create_app().test_client()
    html = client.get("/").get_data(as_text=True)
    # base.css first: the composed module must lose the cascade to .cta
    assert css_hrefs(html) == ["/cobrastyle/base.css", "/cobrastyle/index.css"]
    css = client.get("/cobrastyle/index.css")
    assert css.status_code == 200
    assert "dots.svg" in css.get_data(as_text=True)
    assert client.get("/cobrastyle/img/dots.svg").status_code == 200
    assert "/cobrastyle/about.css" in client.get("/about").get_data(as_text=True)
    about_css = client.get("/cobrastyle/about.css").get_data(as_text=True)
    assert "@import" not in about_css  # theme.css is bundled in
    assert "code {" in about_css
    assert "sourceMappingURL=data:application/json;base64," in about_css

    build(
        module.create_app().jinja_env,
        output_dir=project / "static" / "cobrastyle",
        url_prefix="/static/cobrastyle/",
    )

    monkeypatch.setenv("COBRASTYLE_ENV", "prod")
    prod = module.create_app().test_client()
    hrefs = css_hrefs(prod.get("/").get_data(as_text=True))
    assert hrefs
    assert all(href.startswith("/static/cobrastyle/") for href in hrefs)
    built = prod.get(next(href for href in hrefs if "index" in href))
    assert built.status_code == 200
    assert "/static/cobrastyle/img/dots." in built.get_data(as_text=True)
    about_hrefs = css_hrefs(prod.get("/about").get_data(as_text=True))
    built_about = prod.get(next(href for href in about_hrefs if "about" in href)).get_data(as_text=True)
    assert "code{" in built_about  # @import bundled, then minified


def test_fastapi_example(copy_example, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.delenv("COBRASTYLE_ENV", raising=False)
    project = copy_example("fastapi")
    module = load_module(project / "app.py", "cobrastyle_example_fastapi_dev")

    client = TestClient(module.app)
    html = client.get("/").text
    assert css_hrefs(html) == ["/cobrastyle/base.css", "/cobrastyle/index.css"]
    assert client.get("/cobrastyle/index.css").status_code == 200
    assert client.get("/cobrastyle/img/dots.svg").status_code == 200
    assert "/cobrastyle/about.css" in client.get("/about").text
    about_css = client.get("/cobrastyle/about.css").text
    assert "@import" not in about_css  # theme.css is bundled in
    assert "code {" in about_css

    build(
        module.templates.env,
        output_dir=project / "static" / "cobrastyle",
        url_prefix="/static/cobrastyle/",
    )

    monkeypatch.setenv("COBRASTYLE_ENV", "prod")
    prod_module = load_module(project / "app.py", "cobrastyle_example_fastapi_prod")
    prod = TestClient(prod_module.app)
    hrefs = css_hrefs(prod.get("/").text)
    assert hrefs
    assert all(href.startswith("/static/cobrastyle/") for href in hrefs)
    built = prod.get(next(href for href in hrefs if "index" in href))
    assert built.status_code == 200
    assert "/static/cobrastyle/img/dots." in built.text


def test_django_example(copy_example, monkeypatch):
    pytest.importorskip("django")
    from django.core.management import call_command
    from django.test import Client, override_settings

    from cobrastyle.django.runtime import set_runtime

    project = copy_example("django")
    monkeypatch.syspath_prepend(str(project))
    settings_module = importlib.import_module("demo.settings")
    overrides = {name: getattr(settings_module, name) for name in dir(settings_module) if name.isupper()}

    set_runtime(None)
    try:
        with override_settings(**{**overrides, "DEBUG": True}):
            client = Client()
            html = client.get("/").content.decode()  # Jinja2 backend
            assert css_hrefs(html) == ["/cobrastyle/base.css", "/cobrastyle/index.css"]
            about = client.get("/about/").content.decode()  # DTL backend
            assert css_hrefs(about) == ["/cobrastyle/base.css", "/cobrastyle/about.css"]
            assert client.get("/cobrastyle/index.css").status_code == 200
            assert client.get("/cobrastyle/img/dots.svg").status_code == 200
            about_css = client.get("/cobrastyle/about.css").content.decode()
            assert "@import" not in about_css  # theme.css is bundled in
            assert "code {" in about_css
            assert "sourceMappingURL=data:application/json;base64," in about_css

            call_command("cobrastyle_build", verbosity=0)

        manifest = Manifest.load(project / "cobrastyle_static" / "cobrastyle" / "manifest.json")
        set_runtime(None)
        with override_settings(**{**overrides, "DEBUG": False}):
            client = Client()
            assert manifest.modules["index.css"].url in client.get("/").content.decode()
            assert manifest.modules["about.css"].url in client.get("/about/").content.decode()
            assert client.get("/cobrastyle/index.css").status_code == 404  # dev serving is DEBUG only

            call_command("collectstatic", interactive=False, verbosity=0)
        collected = project / "static_root" / manifest.modules["index.css"].url.removeprefix("/static/")
        assert collected.exists()
    finally:
        set_runtime(None)
        for name in [name for name in sys.modules if name == "demo" or name.startswith("demo.")]:
            del sys.modules[name]
