import pytest

django = pytest.importorskip("django")

from django.core.management import call_command  # noqa: E402
from django.template import engines  # noqa: E402
from django.test import Client, override_settings  # noqa: E402

from cobrastyle.manifest import Manifest  # noqa: E402


def project_settings(project, *, debug=True, cobrastyle=None):
    return {
        "DEBUG": debug,
        "BASE_DIR": project,
        "COBRASTYLE": cobrastyle if cobrastyle is not None else {"ROOT": project / "styles"},
        "TEMPLATES": [
            {
                "BACKEND": "django.template.backends.jinja2.Jinja2",
                "DIRS": [str(project / "templates")],
                "APP_DIRS": False,
                "OPTIONS": {"environment": "cobrastyle.django.environment"},
            }
        ],
    }


def test_dev_render_and_serve_view(page_project, extract):
    with override_settings(**project_settings(page_project), ROOT_URLCONF="cobrastyle.django.urls"):
        html = engines["jinja2"].get_template("index.html").render()
        href = extract(r'href="([^"]+)"', html)
        class_name = extract(r'class="([^"]+)"', html)
        assert href == "/cobrastyle/page.css"
        assert class_name.endswith("title")

        client = Client()
        # urlconf mounted at root here; a real project includes it under a prefix
        response = client.get("/page.css")
        assert response.status_code == 200
        assert f".{class_name}" in response.content.decode()
        etag = response.headers["ETag"]

        assert client.get("/page.css", HTTP_IF_NONE_MATCH=etag).status_code == 304
        assert client.get("/missing.css").status_code == 404


def test_serve_view_is_debug_only(page_project):
    with override_settings(
        **project_settings(page_project, debug=False, cobrastyle={"DEV": True, "ROOT": page_project / "styles"}),
        ROOT_URLCONF="cobrastyle.django.urls",
    ):
        assert Client().get("/page.css").status_code == 404


def test_build_command(page_project):
    with override_settings(**project_settings(page_project)):
        call_command("cobrastyle_build", verbosity=0)

    out = page_project / "cobrastyle_static" / "cobrastyle"
    manifest = Manifest.load(out / "manifest.json")
    entry = manifest.modules["page.css"]
    assert entry.url == "/static/cobrastyle/" + entry.file
    assert (out / entry.file).exists()
    assert manifest.pages == {"index.html": ["page.css"]}


def test_prod_render_from_manifest(page_project):
    with override_settings(**project_settings(page_project)):
        call_command("cobrastyle_build", verbosity=0)
    manifest = Manifest.load(page_project / "cobrastyle_static" / "cobrastyle" / "manifest.json")

    with override_settings(**project_settings(page_project, debug=False)):
        html = engines["jinja2"].get_template("index.html").render()

    assert manifest.modules["page.css"].classes["title"] in html
    assert manifest.modules["page.css"].url in html


def test_collectstatic_ships_built_files(page_project):
    with override_settings(**project_settings(page_project)):
        call_command("cobrastyle_build", verbosity=0)
    manifest = Manifest.load(page_project / "cobrastyle_static" / "cobrastyle" / "manifest.json")

    static_root = page_project / "static_root"
    with override_settings(
        **project_settings(page_project, debug=False),
        STATIC_ROOT=str(static_root),
        STATICFILES_DIRS=[("cobrastyle", str(page_project / "cobrastyle_static" / "cobrastyle"))],
    ):
        call_command("collectstatic", interactive=False, verbosity=0)

    # The collected file lands exactly where the manifest URL points
    collected = static_root / manifest.modules["page.css"].url.removeprefix("/static/")
    assert collected.exists()
