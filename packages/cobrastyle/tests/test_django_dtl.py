import re

import pytest

django = pytest.importorskip("django")

from django.core.management import call_command  # noqa: E402
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


def test_composes_from_other_file_links_both(project):
    (project / "templates" / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "button.css" as styles %}'
        '{% cobrastyle_links %}<button class="{{ styles.button }}"></button>'
    )
    with override_settings(**project_settings(project)):
        html = render("index.html")

    assert 'class="button base"' in html
    assert html.index('href="/cobrastyle/base.css"') < html.index('href="/cobrastyle/button.css"')


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
    assert class_name != "title"

    prod = dict(project_settings(project, debug=False))
    prod["COBRASTYLE"] = {"ROOT": project / "styles"}
    with override_settings(**prod):
        html = render("index.html")

    assert re.search(f'class="{class_name}"', html)
