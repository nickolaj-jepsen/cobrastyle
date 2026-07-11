"""Smoke tests keeping every example runnable: dev render + CSS serving, then build + prod boot."""

import importlib
import importlib.util
import posixpath
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


def extract_fragment_url(fragment: str, module: str) -> str:
    """The URL for ``module`` inside a fragment's out-of-band link script."""
    match = re.search(rf'"([^"]*{module}[^"]*\.css)"', fragment)
    assert match, fragment
    return match.group(1)


def resolve_asset_ref(css_href: str, css: str, name: str) -> str:
    """The URL a browser would fetch for the url() reference to ``name`` in the stylesheet at ``css_href``."""
    match = re.search(rf'url\("?([^")]*{name}[^")]*)"?\)', css)
    assert match, css
    ref = match.group(1)
    assert not ref.startswith(("/", "http")), ref  # built references stay relative to the stylesheet
    return posixpath.normpath(posixpath.join(posixpath.dirname(css_href), ref))


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

    # Hot reload is on in dev; the HTMX fragment links its stylesheet out-of-band
    assert 'src="/cobrastyle/__client__.js"' in html
    assert client.get("/cobrastyle/__client__.js").status_code == 200
    assert "tip.css" not in html  # the page itself never links the fragment's module
    fragment = client.get("/fragments/tip").get_data(as_text=True)
    assert '<div hx-swap-oob="beforeend:head"><script>' in fragment
    assert '"/cobrastyle/tip.css"' in fragment

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
    index_href = next(href for href in hrefs if "index" in href)
    built = prod.get(index_href)
    assert built.status_code == 200
    asset_url = resolve_asset_ref(index_href, built.get_data(as_text=True), "dots")
    assert prod.get(asset_url).status_code == 200
    about_hrefs = css_hrefs(prod.get("/about").get_data(as_text=True))
    built_about = prod.get(next(href for href in about_hrefs if "about" in href)).get_data(as_text=True)
    assert "code{" in built_about  # @import bundled, then minified

    # Fragments work from the manifest too: hashed URL, no hot-reload script
    prod_html = prod.get("/").get_data(as_text=True)
    assert "__client__.js" not in prod_html
    prod_fragment = prod.get("/fragments/tip").get_data(as_text=True)
    assert '<div hx-swap-oob="beforeend:head"><script>' in prod_fragment
    tip_url = extract_fragment_url(prod_fragment, "tip")
    assert tip_url.startswith("/static/cobrastyle/")
    assert prod.get(tip_url).status_code == 200


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
    index_href = next(href for href in hrefs if "index" in href)
    built = prod.get(index_href)
    assert built.status_code == 200
    asset_url = resolve_asset_ref(index_href, built.text, "dots")
    assert prod.get(asset_url).status_code == 200


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

            # Hot reload is on in dev; the DTL fragment links its stylesheet out-of-band
            assert 'src="/cobrastyle/__client__.js"' in html
            assert 'src="/cobrastyle/__client__.js"' in about
            assert client.get("/cobrastyle/__client__.js").status_code == 200
            fragment = client.get("/fragments/tip/").content.decode()
            assert '<div hx-swap-oob="beforeend:head"><script>' in fragment
            assert '"/cobrastyle/tip.css"' in fragment

            call_command("cobrastyle_build", verbosity=0)

        manifest = Manifest.load(project / "cobrastyle_static" / "cobrastyle" / "manifest.json")
        set_runtime(None)
        with override_settings(**{**overrides, "DEBUG": False}):
            client = Client()
            assert manifest.modules["index.css"].url in client.get("/").content.decode()
            assert manifest.modules["about.css"].url in client.get("/about/").content.decode()
            assert client.get("/cobrastyle/index.css").status_code == 404  # dev serving is DEBUG only

            # Fragments work from the manifest too: hashed URL, no hot-reload script
            assert "__client__.js" not in client.get("/").content.decode()
            prod_fragment = client.get("/fragments/tip/").content.decode()
            assert manifest.modules["tip.css"].url in prod_fragment

            call_command("collectstatic", interactive=False, verbosity=0)
        collected = project / "static_root" / manifest.modules["index.css"].url.removeprefix("/static/")
        assert collected.exists()
        # The finder ships the build output but never its manifest
        assert not (project / "static_root" / "cobrastyle" / "manifest.json").exists()

        # Under a hashed storage the same pages serve storage-hashed URLs, both engines
        set_runtime(None)
        with override_settings(
            **{**overrides, "DEBUG": False},
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
            },
        ):
            call_command("collectstatic", interactive=False, verbosity=0, clear=True)
            client = Client()
            for page, module in [("/", "index.css"), ("/about/", "about.css")]:
                html = client.get(page).content.decode()
                href = next(h for h in css_hrefs(html) if module.removesuffix(".css") in h)
                assert href != manifest.modules[module].url
                assert (project / "static_root" / href.removeprefix("/static/")).exists()
    finally:
        set_runtime(None)
        for name in [name for name in sys.modules if name == "demo" or name.startswith("demo.")]:
            del sys.modules[name]
