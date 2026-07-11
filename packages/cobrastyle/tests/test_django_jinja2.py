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
        assert class_name.startswith("page_title_")  # readable dev class names

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


FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "cobrastyle.django.finders.CobrastyleFinder",
]

HASHED_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
}


def test_finder_exposes_built_files_but_not_the_manifest(page_project):
    from cobrastyle.django.finders import CobrastyleFinder

    with override_settings(**project_settings(page_project)):
        call_command("cobrastyle_build", verbosity=0)
        out = page_project / "cobrastyle_static" / "cobrastyle"
        manifest = Manifest.load(out / "manifest.json")
        built = manifest.modules["page.css"].file

        finder = CobrastyleFinder()
        assert finder.find(f"cobrastyle/{built}") == str(out / built)
        assert finder.find(f"cobrastyle/{built}", find_all=True) == [str(out / built)]
        assert finder.find("cobrastyle/manifest.json") is None
        assert finder.find("cobrastyle/missing.css") is None
        assert finder.find("cobrastyle/../styles/page.css") is None
        assert finder.find("elsewhere/page.css") is None

        listed = [path for path, _storage in finder.list(None)]
        assert built in listed
        assert "manifest.json" not in listed


def test_finder_lists_nothing_before_a_build(page_project):
    from cobrastyle.django.finders import CobrastyleFinder

    with override_settings(**project_settings(page_project)):
        finder = CobrastyleFinder()
        assert list(finder.list(None)) == []
        assert finder.find("cobrastyle/page.css") is None


def test_prod_urls_resolve_through_hashed_storage(page_project, extract):
    with override_settings(**project_settings(page_project)):
        call_command("cobrastyle_build", verbosity=0)
    manifest = Manifest.load(page_project / "cobrastyle_static" / "cobrastyle" / "manifest.json")
    entry = manifest.modules["page.css"]

    static_root = page_project / "static_root"
    with override_settings(
        **project_settings(page_project, debug=False),
        STATIC_ROOT=str(static_root),
        STATICFILES_FINDERS=FINDERS,
        STORAGES=HASHED_STORAGES,
    ):
        call_command("collectstatic", interactive=False, verbosity=0)
        html = engines["jinja2"].get_template("index.html").render()

        href = extract(r'href="([^"]+)"', html)
        # The link carries the storage's re-hashed name, not the URL baked at build time
        assert href != entry.url
        assert href.startswith("/static/cobrastyle/page.")
        assert (static_root / href.removeprefix("/static/")).exists()
        assert not (static_root / "cobrastyle" / "manifest.json").exists()


def test_immutable_file_test_recognizes_hashed_names():
    from cobrastyle.django import immutable_file_test

    # cobrastyle's 10-hex names, only under the configured static prefix
    assert immutable_file_test("", "/static/cobrastyle/button.0123456789.css")
    assert immutable_file_test("", "/static/cobrastyle/img/icon.abcdef0123.svg")
    assert not immutable_file_test("", "/static/cobrastyle/button.css")
    assert not immutable_file_test("", "/static/app.0123456789.css")
    # Django's 12-hex hashed names keep their headers anywhere
    assert immutable_file_test("", "/static/app.0123456789ab.css")
    assert immutable_file_test("", "/static/cobrastyle/button.0123456789.0123456789ab.css")
    assert not immutable_file_test("", "/static/app.css")


def test_immutable_file_test_follows_the_configured_prefix():
    from cobrastyle.django import immutable_file_test

    with override_settings(COBRASTYLE={"STATIC_PREFIX": "assets/css"}):
        assert immutable_file_test("", "/static/assets/css/button.0123456789.css")
        assert not immutable_file_test("", "/static/cobrastyle/button.0123456789.css")

    with override_settings(COBRASTYLE={"STATIC_PREFIX": None}):
        # Integration off: only Django-hashed names are immutable
        assert not immutable_file_test("", "/static/cobrastyle/button.0123456789.css")
        assert immutable_file_test("", "/static/app.0123456789ab.css")


def test_build_url_prefix_opts_out_of_static_mapping(page_project, extract):
    config = {"ROOT": page_project / "styles", "BUILD_URL_PREFIX": "https://cdn.example.com/assets/"}
    with override_settings(**project_settings(page_project, cobrastyle=config)):
        call_command("cobrastyle_build", verbosity=0)
    manifest = Manifest.load(page_project / "cobrastyle_static" / "cobrastyle" / "manifest.json")

    with override_settings(**project_settings(page_project, debug=False, cobrastyle=config)):
        html = engines["jinja2"].get_template("index.html").render()

    href = extract(r'href="([^"]+)"', html)
    assert href == manifest.modules["page.css"].url
    assert href.startswith("https://cdn.example.com/assets/")
