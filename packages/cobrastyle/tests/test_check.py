import textwrap

import pytest
from click.testing import CliRunner
from jinja2 import DictLoader, Environment

from cobrastyle.build import BuildError, build
from cobrastyle.check import UsageCollector, check_jinja2
from cobrastyle.cli import cli
from cobrastyle.jinja2 import CobrastyleExtension, configure
from cobrastyle.resolvers import InMemoryResolver


def run_check(templates: dict[str, str], styles: dict[str, str], **kwargs):
    env = Environment(loader=DictLoader(templates), extensions=[CobrastyleExtension])
    configure(env, resolver=InMemoryResolver(styles), module_pattern="[local]")
    collector = UsageCollector()
    check_jinja2(env, collector, **kwargs)
    return collector.report()


def test_valid_references_pass():
    report = run_check(
        {
            "index.html": '{% cobrastyle styles = "page.css" %}'
            '<h1 class="{{ styles.title }}">{{ styles["sub-title"] }}</h1>'
        },
        {"page.css": ".title { color: red } .sub-title { color: blue }"},
    )

    assert report.ok
    assert report.unused == {}
    assert report.references == 2
    assert report.failures() == []


def test_check_resolves_from_the_manifest_in_prod_mode(tmp_path):
    # The CLI promises: class maps come from the built manifest when the target
    # is configured in prod mode — no compiler needed.
    templates = {"index.html": '{% cobrastyle styles = "page.css" %}{{ styles.buttom }}'}
    dev = Environment(loader=DictLoader(templates), extensions=[CobrastyleExtension])
    configure(
        dev,
        resolver=InMemoryResolver({"page.css": ".title { color: red } .button { color: blue }"}),
        module_pattern="[local]",
    )
    build(dev, output_dir=tmp_path)

    prod = Environment(loader=DictLoader(templates), extensions=[CobrastyleExtension])
    configure(prod, manifest=tmp_path / "manifest.json")
    collector = UsageCollector()
    check_jinja2(prod, collector)
    report = collector.report()

    [problem] = report.unknown
    assert problem.attr == "buttom"
    assert problem.suggestion == "button"
    assert "page.css" in report.unused  # title and button both unreferenced


def test_reference_before_the_binding_is_not_flagged():
    # jinja resolves the pre-assignment load from the render context, so a
    # same-named context variable makes this a valid template
    report = run_check(
        {"index.html": '{{ styles.extra }}\n{% cobrastyle styles = "page.css" %}{{ styles.title }}'},
        {"page.css": ".title { color: red }"},
    )

    assert report.ok
    assert report.unused == {}


def test_unknown_class_reports_location_and_suggestion():
    report = run_check(
        {"index.html": '{% cobrastyle styles = "page.css" %}\n<b class="{{ styles.buttom }}">x</b>'},
        {"page.css": ".button { color: red }"},
    )

    [problem] = report.unknown
    assert (problem.template, problem.lineno) == ("index.html", 2)
    assert problem.attr == "buttom"
    assert problem.suggestion == "button"
    assert problem.modules == ("page.css",)
    assert "did you mean 'button'?" in str(problem)


def test_constant_subscript_is_checked_like_getattr():
    report = run_check(
        {"index.html": '{% cobrastyle styles = "page.css" %}{{ styles["buttom"] }}'},
        {"page.css": ".button { color: red }"},
    )

    [problem] = report.unknown
    assert problem.reference == 'styles["buttom"]'


def test_dynamic_subscript_is_unverifiable_and_suppresses_unused():
    report = run_check(
        {"index.html": '{% cobrastyle styles = "page.css" %}{{ styles[name] }}'},
        {"page.css": ".title { color: red } .ghost { color: gray }"},
    )

    assert report.ok
    assert report.unused == {}


def test_set_rebinding_suppresses_checks():
    report = run_check(
        {"index.html": '{% cobrastyle styles = "page.css" %}{% set styles = {"x": 1} %}{{ styles.anything }}'},
        {"page.css": ".title { color: red }"},
    )

    assert report.ok
    assert report.unused == {}


def test_macro_parameter_shadowing_is_not_confused():
    report = run_check(
        {
            "index.html": '{% cobrastyle styles = "page.css" %}'
            "{% macro chip(styles) %}{{ styles.bogus }}{% endmacro %}"
            "{{ chip({}) }}{{ styles.title }}"
        },
        {"page.css": ".title { color: red }"},
    )

    assert report.ok
    assert report.unused == {}


def test_passing_the_map_around_marks_all_classes_used():
    report = run_check(
        {"index.html": '{% cobrastyle styles = "page.css" %}{{ styles.title }}{% set copy = styles %}'},
        {"page.css": ".title { color: red } .ghost { color: gray }"},
    )

    assert report.ok
    assert report.unused == {}


def test_child_block_usage_counts_but_is_never_flagged():
    report = run_check(
        {
            "base.html": '{% cobrastyle styles = "page.css" %}{% block content %}{% endblock %}',
            "child.html": '{% extends "base.html" %}'
            "{% block content %}{{ styles.title }}{{ styles.nope }}{% endblock %}",
        },
        {"page.css": ".title { color: red } .ghost { color: gray }"},
    )

    # 'nope' is unverifiable across templates, but 'title' still counts as used
    assert report.ok
    assert report.unused == {"page.css": ["ghost"]}


def test_unused_exports_are_reported():
    report = run_check(
        {"index.html": '{% cobrastyle styles = "page.css" %}{{ styles.title }}'},
        {"page.css": ".title { color: red } .ghost { color: gray } .zombie { color: green }"},
    )

    assert report.ok
    assert report.unused == {"page.css": ["ghost", "zombie"]}


def test_composed_from_classes_count_as_used():
    report = run_check(
        {
            "index.html": '{% cobrastyle b = "button.css" %}{{ b.button }}',
            "admin.html": '{% cobrastyle base = "base.css" %}x',
        },
        {
            "base.css": ".base { color: black } .lonely { color: gray }",
            "button.css": '.button { composes: base from "./base.css"; background: red }',
        },
    )

    assert report.ok
    assert report.unused == {"base.css": ["lonely"]}


def test_underscore_alias_counts_for_dashed_class():
    report = run_check(
        {"index.html": '{% cobrastyle styles = "page.css" %}{{ styles.hello_world }}'},
        {"page.css": ".hello-world { color: red }"},
    )

    assert report.ok
    assert report.unused == {}


def test_dict_api_access_is_treated_as_dynamic():
    report = run_check(
        {"index.html": '{% cobrastyle styles = "page.css" %}{% for k in styles.items() %}{{ k }}{% endfor %}'},
        {"page.css": ".title { color: red } .ghost { color: gray }"},
    )

    assert report.ok
    assert report.unused == {}


def test_dict_shadowed_class_is_flagged_with_subscript_suggestion():
    # jinja getattr resolves dict attributes first: styles.items yields the method, never the class
    report = run_check(
        {"index.html": '{% cobrastyle styles = "page.css" %}{{ styles.items }}'},
        {"page.css": ".items { color: red }"},
    )

    [problem] = report.unknown
    assert problem.shadowed
    assert problem.suggestion == 'styles["items"]'
    assert "shadowed by the dict API" in str(problem)


def test_dict_shadowed_class_is_fine_via_subscript():
    report = run_check(
        {"index.html": '{% cobrastyle styles = "page.css" %}{{ styles["items"] }}'},
        {"page.css": ".items { color: red }"},
    )

    assert report.ok
    assert report.unused == {}


def test_name_bound_twice_validates_against_the_union():
    report = run_check(
        {
            "index.html": '{% cobrastyle styles = "a.css" %}{{ styles.aa }}'
            '{% cobrastyle styles = "b.css" %}{{ styles.bb }}{{ styles.cc }}'
        },
        {"a.css": ".aa { color: red }", "b.css": ".bb { color: blue }"},
    )

    [problem] = report.unknown
    assert problem.attr == "cc"
    assert problem.modules == ("a.css", "b.css")


def test_broken_template_without_cobrastyle_is_skipped(caplog):
    with caplog.at_level("WARNING", logger="cobrastyle.build"):
        report = run_check(
            {
                "index.html": '{% cobrastyle styles = "page.css" %}{{ styles.title }}',
                "broken.html": "{% block %}",
            },
            {"page.css": ".title { color: red }"},
        )

    assert report.ok
    assert report.templates == 1
    assert any("broken.html" in record.message for record in caplog.records)


def test_strict_fails_on_any_broken_template():
    with pytest.raises(BuildError, match=r"broken\.html"):
        run_check(
            {"broken.html": "{% block %}"},
            {},
            strict=True,
        )


def test_missing_stylesheet_is_an_error():
    with pytest.raises(BuildError, match=r"nope\.css"):
        run_check(
            {"index.html": '{% cobrastyle styles = "nope.css" %}'},
            {},
        )


def test_environment_without_extension_is_an_error():
    env = Environment(loader=DictLoader({}))
    with pytest.raises(BuildError, match="not registered"):
        check_jinja2(env, UsageCollector())


@pytest.fixture
def check_project(tmp_path, monkeypatch):
    """A checkable project whose environment factory is importable as ``checkapp:create_environment``."""
    import sys

    monkeypatch.delitem(sys.modules, "checkapp", raising=False)
    (tmp_path / "templates").mkdir()
    (tmp_path / "styles").mkdir()
    (tmp_path / "styles" / "page.css").write_text(".title { color: red; }\n.ghost { color: gray; }")
    (tmp_path / "templates" / "index.html").write_text(
        '{% cobrastyle styles = "page.css" %}<h1 class="{{ styles.title }}">Hi</h1>'
    )
    (tmp_path / "checkapp.py").write_text(
        textwrap.dedent(
            """
            from pathlib import Path

            from jinja2 import Environment, FileSystemLoader

            from cobrastyle import FileSystemResolver
            from cobrastyle.jinja2 import CobrastyleExtension, configure

            HERE = Path(__file__).parent


            def create_environment():
                environment = Environment(loader=FileSystemLoader(HERE / "templates"), extensions=[CobrastyleExtension])
                configure(environment, resolver=FileSystemResolver(HERE / "styles"))
                return environment
            """
        )
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


def invoke_check(*args):
    runner = CliRunner()
    result = runner.invoke(cli, ["check", "checkapp:create_environment", *args])
    output = result.output + (result.stderr if hasattr(result, "stderr") and result.stderr_bytes else "")
    return result, output


def test_cli_check_warns_about_unused_but_passes(check_project):
    result, output = invoke_check()

    assert result.exit_code == 0, output
    assert "OK" in output
    assert "ghost" in output  # unused warning, not an error


def test_cli_check_strict_unused_fails(check_project):
    result, output = invoke_check("--strict-unused")

    assert result.exit_code == 1
    assert "unused" in output


def test_cli_check_fails_on_unknown_class(check_project):
    (check_project / "templates" / "index.html").write_text(
        '{% cobrastyle styles = "page.css" %}{{ styles.title }}{{ styles.ghost }}\n{{ styles.tilte }}'
    )

    result, output = invoke_check()

    assert result.exit_code == 1
    assert "index.html:2" in output
    assert "tilte" in output
    assert "did you mean 'title'?" in output
