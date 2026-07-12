from io import StringIO

import pytest

django = pytest.importorskip("django")

from django.core.management import CommandError, call_command  # noqa: E402
from django.test import override_settings  # noqa: E402


@pytest.fixture
def project(tmp_path):
    templates = tmp_path / "templates"
    styles = tmp_path / "styles"
    templates.mkdir()
    styles.mkdir()

    (styles / "page.css").write_text(".title { color: red; }\n.ghost { color: gray; }")
    (templates / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}'
        "<head>{% cobrastyle_links %}</head>"
        '<h1 class="{{ styles.title }}">Hi</h1>'
    )
    return tmp_path


def project_settings(project, *, debug=True):
    return {
        "DEBUG": debug,
        "BASE_DIR": project,
        "COBRASTYLE": {"ROOT": project / "styles", "MODULE_PATTERN": "[local]"},
        "TEMPLATES": [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(project / "templates")],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ],
    }


def run_check(project, settings=None, **options):
    stdout, stderr = StringIO(), StringIO()
    with override_settings(**(settings or project_settings(project))):
        call_command("cobrastyle_check", stdout=stdout, stderr=stderr, **options)
    return stdout.getvalue(), stderr.getvalue()


def test_check_command_warns_about_unused_but_passes(project):
    stdout, stderr = run_check(project)

    assert "OK" in stdout
    assert "ghost" in stderr


def test_check_command_strict_unused_fails(project):
    with pytest.raises(CommandError, match="unused"):
        run_check(project, strict_unused=True)


def test_check_command_fails_on_unknown_class(project):
    (project / "templates" / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}{{ styles.ghost }}\n{{ styles.tilte }}'
    )

    stderr = StringIO()
    with (
        override_settings(**project_settings(project)),
        pytest.raises(CommandError, match="unknown class"),
    ):
        call_command("cobrastyle_check", stderr=stderr)

    assert "index.html:2" in stderr.getvalue()
    assert "did you mean 'title'?" in stderr.getvalue()


def test_for_loop_rebinding_is_not_confused(project):
    (project / "templates" / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}'
        "{% for styles in things %}{{ styles.bogus }}{% endfor %}"
    )

    stdout, _ = run_check(project)

    assert "OK" in stdout


def test_with_rebinding_is_not_confused(project):
    (project / "templates" / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}'
        "{% with styles=other %}{{ styles.bogus }}{% endwith %}"
    )

    stdout, _ = run_check(project)

    assert "OK" in stdout


def test_bare_variable_escape_marks_all_used(project):
    (project / "templates" / "extra.html").write_text("x")
    (project / "templates" / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}{% include "extra.html" with s=styles %}'
    )

    stdout, stderr = run_check(project)

    assert "OK" in stdout
    assert "ghost" not in stderr  # the map escaped; no unused report for it


def test_usage_inside_cx_and_if_counts(project):
    (project / "templates" / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}'
        "{% if styles.ghost %}{% cx styles.title %}{% endif %}"
    )

    stdout, stderr = run_check(project)

    assert "OK" in stdout
    assert "unused" not in stderr


def test_child_block_usage_counts_but_is_never_flagged(project):
    (project / "templates" / "base.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}{% block content %}{% endblock %}'
    )
    (project / "templates" / "child.html").write_text(
        '{% extends "base.html" %}{% block content %}{{ styles.ghost }}{{ styles.nope }}{% endblock %}'
    )

    stdout, stderr = run_check(project)

    assert "OK" in stdout
    assert "unused" not in stderr  # title used in index.html, ghost in the child block


def test_mixed_engines_share_one_usage_report(project):
    (project / "templates_jinja").mkdir()
    (project / "templates_jinja" / "jpage.html").write_text('{% cobrastyle styles = "page.css" %}{{ styles.ghost }}')
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

    stdout, stderr = run_check(project, settings=settings)

    # index.html (DTL) uses title, jpage.html (Jinja2) uses ghost — nothing unused
    assert "OK" in stdout
    assert "unused" not in stderr


def test_filter_argument_usage_counts_and_typos_are_flagged(project):
    (project / "templates" / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}{{ styles.title }}{{ extra|default:styles.ghost }}'
    )

    stdout, stderr = run_check(project)
    assert "OK" in stdout
    assert "unused" not in stderr  # ghost is used as a filter argument

    (project / "templates" / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}{{ extra|default:styles.tilte }}'
    )
    with pytest.raises(CommandError, match="unknown class"):
        run_check(project)


def test_regroup_rebinding_is_not_confused(project):
    (project / "templates" / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}{{ styles.title }}'
        "{% regroup people by kind as styles %}{{ styles.0.grouper }}"
    )

    stdout, _ = run_check(project)

    assert "OK" in stdout


def test_dtl_dot_access_reaches_dict_method_named_class(project):
    # DTL resolution is subscript-first, so styles.items yields the class, unlike jinja
    (project / "styles" / "page.css").write_text(".items { color: red; }")
    (project / "templates" / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}{{ styles.items }}'
    )

    stdout, stderr = run_check(project)

    assert "OK" in stdout
    assert "unused" not in stderr


def test_dtl_class_named_like_dict_method_keeps_unused_reporting(project):
    # A plain class reference (key-first resolution) must not exempt the module
    # from unused reporting the way whole-map dict-API use does
    (project / "styles" / "page.css").write_text(".items { color: red; } .orphan { color: blue; }")
    (project / "templates" / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}{{ styles.items }}'
    )

    stdout, stderr = run_check(project)

    assert "OK" in stdout
    assert "orphan" in stderr


def test_dtl_dict_api_dot_access_is_not_flagged(project):
    # styles.items resolves to the bound dict method when no such class exists —
    # a valid template (iterating the class map), not an unknown-class reference
    (project / "styles" / "page.css").write_text(".title { color: red; }")
    (project / "templates" / "index.html").write_text(
        '{% load cobrastyle %}{% cobrastyle "page.css" as styles %}'
        "{% for name, cls in styles.items %}{{ name }}{% endfor %}{{ styles.keys }}"
    )

    stdout, stderr = run_check(project)

    assert "OK" in stdout
    assert "unused" not in stderr


def test_rendering_after_check_still_links_stylesheets(project):
    from django.template import engines

    settings = project_settings(project)
    with override_settings(**settings):
        assert "page.css" in engines["django"].get_template("index.html").render({})
        run_check(project, settings=settings)
        # The cached parse under the check's throwaway runtime must not leak into later renders
        assert "page.css" in engines["django"].get_template("index.html").render({})


def test_check_command_requires_an_engine(project):
    settings = project_settings(project)
    settings["TEMPLATES"] = []

    with pytest.raises(CommandError, match="No usable template engine"):
        run_check(project, settings=settings)


def test_check_command_strict_fails_on_broken_template(project):
    (project / "templates" / "broken.html").write_text("{% bogus %}")

    with pytest.raises(CommandError, match=r"broken\.html"):
        run_check(project, strict=True)
