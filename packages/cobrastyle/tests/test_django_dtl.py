import re

import pytest

django = pytest.importorskip("django")

from django.core.management import CommandError, call_command  # noqa: E402
from django.template import TemplateSyntaxError, engines  # noqa: E402
from django.test import Client, override_settings  # noqa: E402

from cobrastyle.django.runtime import get_runtime  # noqa: E402
from cobrastyle.manifest import Manifest  # noqa: E402


@pytest.fixture
def project(tmp_path):
    templates = tmp_path / "templates"
    styles = tmp_path / "styles"
    templates.mkdir()
    styles.mkdir()

    (styles / "page.css").write_text(".title { color: red; }")
    (styles / "base.css").write_text(".base { color: black; }")
    (styles / "button.css").write_text('.button { composes: base from "./base.css"; background: red; }')

    (templates / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}'
        "<head>{% cobrastyle_links %}</head>"
        '<h1 class="{{ styles.title }}">Hi</h1>'
    )
    return tmp_path


def project_settings(project, *, debug=True, cobrastyle=None):
    return {
        "DEBUG": debug,
        "BASE_DIR": project,
        "COBRASTYLE": cobrastyle
        if cobrastyle is not None
        else {"ROOT": project / "styles", "MODULE_PATTERN": "[local]"},
        "TEMPLATES": [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(project / "templates")],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ],
    }


def render(name, context=None):
    return engines["django"].get_template(name).render(context or {})


def test_assignment_and_links(project):
    with override_settings(**project_settings(project)):
        html = render("index.html")

    assert '<h1 class="title">Hi</h1>' in html
    assert '<link rel="stylesheet" href="/cobrastyle/page.css" />' in html


def test_class_name_rewrite(project):
    (project / "styles" / "page.css").write_text(".header-title { color: red; }")
    (project / "templates" / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}1:{{ styles.header_title }}'
    )
    with override_settings(**project_settings(project)):
        assert "1:header-title" in render("index.html")


def test_composes_from_other_file_is_bundled(project):
    (project / "templates" / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "button.css" as styles %}'
        '{% cobrastyle_links %}<button class="{{ styles.button }}"></button>'
    )
    with override_settings(**project_settings(project)):
        html = render("index.html")

    # The composed-from module's rules are bundled into button.css, not linked separately
    assert 'class="button base"' in html
    assert 'href="/cobrastyle/button.css"' in html
    assert "base.css" not in html


def test_links_with_inherited_head(project):
    (project / "templates" / "base.html").write_text(
        "{% load cobrastyle %}<head>{% cobrastyle_links %}</head><body>{% block content %}{% endblock %}</body>"
    )
    (project / "templates" / "child.html").write_text(
        '{% extends "base.html" %}{% load cobrastyle %}{% block content %}'
        '{% cobrastyle "page.css" as styles %}<h1 class="{{ styles.title }}">Hi</h1>{% endblock %}'
    )
    with override_settings(**project_settings(project)):
        html = render("child.html")

    assert '<head><link rel="stylesheet" href="/cobrastyle/page.css" /></head>' in html
    assert '<h1 class="title">Hi</h1>' in html


def test_include_limitation_and_escape_hatch(project):
    (project / "styles" / "nav.css").write_text(".nav { color: blue; }")
    (project / "templates" / "nav.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "nav.css" as styles %}<nav class="{{ styles.nav }}"></nav>'
    )
    (project / "templates" / "plain_page.html").write_text(
        '{% load cobrastyle %}{% cobrastyle_links %}{% include "nav.html" %}'
    )
    (project / "templates" / "escape_page.html").write_text(
        '{% load cobrastyle %}{% cobrastyle_links "nav.css" %}{% include "nav.html" %}'
    )
    with override_settings(**project_settings(project)):
        assert "nav.css" not in render("plain_page.html").split("<nav")[0]
        assert '<link rel="stylesheet" href="/cobrastyle/nav.css" />' in render("escape_page.html")


def test_dynamic_path_is_rejected(project):
    (project / "templates" / "bad.html").write_text("{% load cobrastyle %}{% cobrastyle somevar as styles %}")
    with override_settings(**project_settings(project)), pytest.raises(TemplateSyntaxError, match="constant string"):
        render("bad.html")


def test_missing_stylesheet_is_parse_error(project):
    (project / "templates" / "bad.html").write_text('{% load cobrastyle %}{% cobrastyle "nope.css" as styles %}')
    with override_settings(**project_settings(project)), pytest.raises(Exception, match="nope"):
        render("bad.html")


def test_serve_view_works_for_dtl_only_project(project):
    with override_settings(**project_settings(project), ROOT_URLCONF="cobrastyle.django.urls"):
        render("index.html")
        response = Client().get("/page.css")

    assert response.status_code == 200
    assert ".title" in response.content.decode()


def test_build_command_covers_dtl_templates(project):
    with override_settings(**project_settings(project)):
        call_command("cobrastyle_build", verbosity=0)

    out = project / "cobrastyle_static" / "cobrastyle"
    manifest = Manifest.load(out / "manifest.json")
    assert manifest.pages["index.html"] == ["page.css"]
    entry = manifest.modules["page.css"]
    assert (out / entry.file).exists()


def test_prod_render_from_manifest(project):
    with override_settings(**project_settings(project)):
        call_command("cobrastyle_build", verbosity=0)
    manifest = Manifest.load(project / "cobrastyle_static" / "cobrastyle" / "manifest.json")

    with override_settings(**project_settings(project, debug=False)):
        html = render("index.html")
        assert manifest.modules["page.css"].url in html

        # a worker whose templates came from a cache never parsed here — links must fall back to manifest.pages
        get_runtime().pages.clear()
        html = render("index.html")
        assert manifest.modules["page.css"].url in html


def test_prod_urls_resolve_through_hashed_storage(project):
    with override_settings(**project_settings(project)):
        call_command("cobrastyle_build", verbosity=0)
    manifest = Manifest.load(project / "cobrastyle_static" / "cobrastyle" / "manifest.json")
    entry = manifest.modules["page.css"]

    static_root = project / "static_root"
    with override_settings(
        **project_settings(project, debug=False),
        STATIC_ROOT=str(static_root),
        STATICFILES_FINDERS=["cobrastyle.django.finders.CobrastyleFinder"],
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
        },
    ):
        call_command("collectstatic", interactive=False, verbosity=0)
        html = render("index.html")

        match = re.search(r'href="([^"]+)"', html)
        assert match is not None
        href = match.group(1)
        # The DTL runtime maps through the storage's re-hashed name too
        assert href != entry.url
        assert href.startswith("/static/cobrastyle/page.")
        assert (static_root / href.removeprefix("/static/")).exists()


def test_prod_missing_module_is_parse_error(project):
    with override_settings(**project_settings(project)):
        call_command("cobrastyle_build", verbosity=0)

    (project / "templates" / "new.html").write_text('{% load cobrastyle %}{% cobrastyle "brand-new.css" as s %}')
    with (
        override_settings(**project_settings(project, debug=False)),
        pytest.raises(TemplateSyntaxError, match="cobrastyle_build"),
    ):
        render("new.html")


def test_mixed_engines_build_merges_pages(project):
    (project / "templates_jinja").mkdir()
    (project / "templates_jinja" / "jpage.html").write_text(
        '{% cobrastyle styles = "page.css" %}{{ cobrastyle.links() }}'
    )
    settings = project_settings(project)
    settings["TEMPLATES"] = [
        *settings["TEMPLATES"],
        {
            "BACKEND": "django.template.backends.jinja2.Jinja2",
            "DIRS": [str(project / "templates_jinja")],
            "APP_DIRS": False,
            "OPTIONS": {"environment": "cobrastyle.django.environment"},
        },
    ]
    with override_settings(**settings):
        call_command("cobrastyle_build", verbosity=0)

    manifest = Manifest.load(project / "cobrastyle_static" / "cobrastyle" / "manifest.json")
    assert manifest.pages["index.html"] == ["page.css"]
    assert manifest.pages["jpage.html"] == ["page.css"]


def test_prod_render_uses_hashed_classes(project):
    hashed = dict(project_settings(project))
    hashed["COBRASTYLE"] = {"ROOT": project / "styles"}  # default hashed module pattern
    with override_settings(**hashed):
        call_command("cobrastyle_build", verbosity=0)
    manifest = Manifest.load(project / "cobrastyle_static" / "cobrastyle" / "manifest.json")
    class_name = manifest.modules["page.css"].classes["title"]
    assert re.fullmatch(r"[\w-]+_title", class_name)
    assert not class_name.startswith("page_")  # not the readable dev pattern

    prod = dict(project_settings(project, debug=False))
    prod["COBRASTYLE"] = {"ROOT": project / "styles"}
    with override_settings(**prod):
        html = render("index.html")

    assert re.search(f'class="{class_name}"', html)


_CX_TEMPLATES = {
    "TEMPLATES": [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [],
            "APP_DIRS": False,
            "OPTIONS": {},
        }
    ]
}


def _render_cx(source, context=None):
    with override_settings(**_CX_TEMPLATES):
        return engines["django"].from_string("{% load cobrastyle %}" + source).render(context or {})


def test_cx_tag_conditionals():
    out = _render_cx(
        "{% cx 'btn' 'active' if on 'lg' if big else 'sm' %}",
        {"on": True, "big": False},
    )
    assert out == "btn active sm"


def test_cx_tag_drops_falsy_and_supports_maps():
    out = _render_cx("{% cx base extra flags %}", {"base": "btn", "extra": "", "flags": {"a": True, "b": False}})
    assert out == "btn a"


def test_cx_tag_escapes_unsafe_values():
    out = _render_cx("{% cx evil %}", {"evil": 'a" onload="x'})
    assert out == "a&quot; onload=&quot;x"


def test_cx_tag_requires_a_term():
    with pytest.raises(TemplateSyntaxError):
        _render_cx("{% cx %}")


def test_build_command_options(project):
    (project / "styles" / "admin.css").write_text(".panel { color: red; }")
    (project / "templates" / "admin").mkdir()
    (project / "templates" / "admin" / "panel.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "admin.css" as styles %}{{ styles.panel }}'
    )
    out = project / "custom_out"
    with override_settings(**project_settings(project)):
        call_command(
            "cobrastyle_build", out=str(out), url_prefix="/cdn/", globs=["admin/*.html"], no_minify=True, verbosity=0
        )

    manifest = Manifest.load(out / "manifest.json")
    assert set(manifest.pages) == {"admin/panel.html"}
    entry = manifest.modules["admin.css"]
    assert entry.url == "/cdn/" + entry.file
    assert "color: red" in (out / entry.file).read_text()  # readable, not minified


def test_build_command_clean(project):
    with override_settings(**project_settings(project)):
        call_command("cobrastyle_build", verbosity=0)
        out = project / "cobrastyle_static" / "cobrastyle"
        stale = next(out.glob("page.*.css"))
        (project / "styles" / "page.css").write_text(".title { color: green; }")

        call_command("cobrastyle_build", clean=True, verbosity=0)

    assert not stale.exists()
    manifest = Manifest.load(out / "manifest.json")
    assert "green" in (out / manifest.modules["page.css"].file).read_text()


def test_build_command_strict_fails_on_broken_dtl_template(project):
    (project / "templates" / "broken.html").write_text("{% bogus %}")

    with (
        override_settings(**project_settings(project)),
        pytest.raises(CommandError, match=r"broken\.html"),
    ):
        call_command("cobrastyle_build", strict=True, verbosity=0)


def test_broken_dtl_template_without_cobrastyle_is_skipped(project, caplog):
    (project / "templates" / "broken.html").write_text("{% bogus %}")

    with override_settings(**project_settings(project)), caplog.at_level("WARNING", logger="cobrastyle.build"):
        call_command("cobrastyle_build", verbosity=0)

    manifest = Manifest.load(project / "cobrastyle_static" / "cobrastyle" / "manifest.json")
    assert "broken.html" not in manifest.pages
    assert any("broken.html" in record.message for record in caplog.records)


def test_build_command_requires_an_engine(project):
    settings = project_settings(project)
    settings["TEMPLATES"] = []

    with override_settings(**settings), pytest.raises(CommandError, match="No usable template engine"):
        call_command("cobrastyle_build", verbosity=0)


def test_first_template_dir_wins(project):
    second = project / "templates2"
    second.mkdir()
    (project / "styles" / "shadow.css").write_text(".shadow { color: red; }")
    (second / "index.html").write_text('{% load cobrastyle %}{% cobrastyle "shadow.css" as styles %}')
    settings = project_settings(project)
    settings["TEMPLATES"][0]["DIRS"].append(str(second))

    with override_settings(**settings):
        call_command("cobrastyle_build", verbosity=0)

    manifest = Manifest.load(project / "cobrastyle_static" / "cobrastyle" / "manifest.json")
    assert manifest.pages["index.html"] == ["page.css"]
    assert "shadow.css" not in manifest.modules


def test_app_dirs_templates_are_collected(project, monkeypatch):
    app_dir = project / "dtlapp"
    (app_dir / "templates").mkdir(parents=True)
    (app_dir / "__init__.py").write_text("")
    (app_dir / "templates" / "apppage.html").write_text('{% load cobrastyle %}{% cobrastyle "page.css" as styles %}')
    monkeypatch.syspath_prepend(str(project))
    settings = project_settings(project)
    settings["TEMPLATES"][0]["APP_DIRS"] = True
    settings["INSTALLED_APPS"] = ["django.contrib.staticfiles", "cobrastyle.django", "dtlapp"]

    with override_settings(**settings):
        call_command("cobrastyle_build", verbosity=0)

    manifest = Manifest.load(project / "cobrastyle_static" / "cobrastyle" / "manifest.json")
    assert set(manifest.pages) == {"index.html", "apppage.html"}


def test_manifest_object_in_settings(project):
    with override_settings(**project_settings(project)):
        call_command("cobrastyle_build", verbosity=0)
    manifest = Manifest.load(project / "cobrastyle_static" / "cobrastyle" / "manifest.json")

    config = {"ROOT": project / "styles", "MODULE_PATTERN": "[local]", "MANIFEST": manifest}
    with override_settings(**project_settings(project, debug=False, cobrastyle=config)):
        html = render("index.html")

    assert manifest.modules["page.css"].file in html


def test_prod_links_escape_hatch_missing_module_is_render_error(project):
    with override_settings(**project_settings(project)):
        call_command("cobrastyle_build", verbosity=0)

    (project / "templates" / "hatch.html").write_text('{% load cobrastyle %}{% cobrastyle_links "ghost.css" %}')
    with (
        override_settings(**project_settings(project, debug=False)),
        pytest.raises(TemplateSyntaxError, match="cobrastyle_build"),
    ):
        render("hatch.html")


def test_cobrastyle_tag_arity_error(project):
    (project / "templates" / "bad.html").write_text('{% load cobrastyle %}{% cobrastyle "page.css" styles %}')

    with override_settings(**project_settings(project)), pytest.raises(TemplateSyntaxError, match="cobrastyle expects"):
        render("bad.html")


def test_traversal_path_is_rejected(project):
    (project / "templates" / "bad.html").write_text('{% load cobrastyle %}{% cobrastyle "../secret.css" as s %}')

    with override_settings(**project_settings(project)), pytest.raises(TemplateSyntaxError, match="relative"):
        render("bad.html")


def test_cx_tag_parse_errors(project):
    with override_settings(**project_settings(project)):
        engine = engines["django"]
        with pytest.raises(TemplateSyntaxError, match="at least one"):
            engine.from_string("{% load cobrastyle %}{% cx %}")
        with pytest.raises(TemplateSyntaxError, match="'if' must be followed"):
            engine.from_string('{% load cobrastyle %}{% cx "a" if %}')
        with pytest.raises(TemplateSyntaxError, match="'else' must be followed"):
            engine.from_string('{% load cobrastyle %}{% cx "a" if flag else %}')


def test_runtime_ignores_templates_without_an_origin(project):
    from cobrastyle.django.runtime import DTLRuntime
    from cobrastyle.manager import CobrastyleManager
    from cobrastyle.resolvers import FileSystemResolver

    runtime = DTLRuntime(manager=CobrastyleManager(FileSystemResolver(project / "styles")))

    # from_string templates have no origin; recording and lookup are both no-ops
    runtime.record(None, ("page.css",))
    assert runtime.pages == {}
    assert runtime.page_modules(None) == []
