import json
import textwrap

import click
import pytest
from click.testing import CliRunner
from jinja2 import DictLoader, Environment

from cobrastyle.cli import adapt_target, cli, import_target


def make_environment() -> Environment:
    return Environment(loader=DictLoader({}))


def test_adapt_environment():
    environment = make_environment()
    assert adapt_target(environment) is environment


def test_adapt_factory():
    environment = make_environment()
    assert adapt_target(lambda: environment) is environment


def test_adapt_jinja2templates_like():
    environment = make_environment()

    class Templates:
        env = environment

    assert adapt_target(Templates()) is environment


def test_adapt_flask_app():
    flask = pytest.importorskip("flask")
    app = flask.Flask("testapp")
    assert adapt_target(app) is app.jinja_env


def test_adapt_rejects_unknown_object():
    with pytest.raises(click.ClickException, match="Cannot adapt"):
        adapt_target(object())


def test_adapt_rejects_factory_returning_unknown_object():
    with pytest.raises(click.ClickException, match="Cannot adapt"):
        adapt_target(lambda: object())


def test_adapt_factory_returning_templates_like():
    environment = make_environment()

    class Templates:
        env = environment

    assert adapt_target(lambda: Templates()) is environment


def test_adapt_rejects_factory_returning_factory():
    with pytest.raises(click.ClickException, match="another callable"):
        adapt_target(lambda: lambda: make_environment())


def test_import_target_requires_colon():
    with pytest.raises(click.UsageError, match=r"package\.module:attribute"):
        import_target("just_a_module")


def test_import_target_missing_attribute():
    with pytest.raises(click.ClickException, match="no attribute"):
        import_target("jinja2:does_not_exist")


def test_import_target_missing_module():
    with pytest.raises(click.ClickException, match="Cannot import module"):
        import_target("definitely_not_a_module_xyz:attribute")


def test_import_target_dotted_attribute():
    assert import_target("cobrastyle.cli:cli.name") == "cli"


@pytest.fixture
def build_project(tmp_path, monkeypatch):
    """A buildable project whose environment factory is importable as ``buildapp:create_environment``."""
    import sys

    monkeypatch.delitem(sys.modules, "buildapp", raising=False)
    (tmp_path / "templates").mkdir()
    (tmp_path / "styles").mkdir()
    (tmp_path / "styles" / "page.css").write_text(".title { color: red; }")
    (tmp_path / "templates" / "index.html").write_text(
        '{% cobrastyle styles = "page.css" %}{{ cobrastyle.links() }}<h1 class="{{ styles.title }}">Hi</h1>'
    )
    (tmp_path / "buildapp.py").write_text(
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


def load_manifest(project):
    return json.loads((project / "dist" / "manifest.json").read_text())


def test_cli_build_end_to_end(build_project):
    result = CliRunner().invoke(
        cli, ["build", "buildapp:create_environment", "--out", "dist", "--url-prefix", "/assets/"]
    )

    assert result.exit_code == 0, result.output
    assert "1 module(s)" in result.output

    manifest = load_manifest(build_project)
    entry = manifest["modules"]["page.css"]
    assert entry["url"] == "/assets/" + entry["file"]
    assert (build_project / "dist" / entry["file"]).exists()
    assert manifest["pages"] == {"index.html": ["page.css"]}


def test_cli_build_clean_and_no_minify(build_project):
    runner = CliRunner()
    assert runner.invoke(cli, ["build", "buildapp:create_environment", "--out", "dist"]).exit_code == 0
    stale = next((build_project / "dist").glob("page.*.css"))
    (build_project / "styles" / "page.css").write_text(".title { color: green; }")

    result = runner.invoke(cli, ["build", "buildapp:create_environment", "--out", "dist", "--clean", "--no-minify"])

    assert result.exit_code == 0, result.output
    assert not stale.exists()
    built = (build_project / "dist" / load_manifest(build_project)["modules"]["page.css"]["file"]).read_text()
    assert "color: green" in built  # readable, not minified


def test_cli_build_strict_fails_on_any_broken_template(build_project):
    (build_project / "templates" / "broken.html").write_text("{% block %}")

    result = CliRunner().invoke(cli, ["build", "buildapp:create_environment", "--out", "dist", "--strict"])

    assert result.exit_code != 0
    assert "broken.html" in result.output


def test_cli_build_glob_filters(build_project):
    (build_project / "templates" / "admin").mkdir()
    (build_project / "templates" / "admin" / "panel.html").write_text('{% cobrastyle styles = "page.css" %}x')

    result = CliRunner().invoke(
        cli, ["build", "buildapp:create_environment", "--out", "dist", "--glob", "admin/*.html"]
    )

    assert result.exit_code == 0, result.output
    assert load_manifest(build_project)["pages"] == {"admin/panel.html": ["page.css"]}


def test_cli_build_extra_templates(build_project):
    (build_project / "templates" / "special.htm").write_text('{% cobrastyle styles = "page.css" %}x')

    result = CliRunner().invoke(
        cli, ["build", "buildapp:create_environment", "--out", "dist", "--template", "special.htm"]
    )

    assert result.exit_code == 0, result.output
    assert set(load_manifest(build_project)["pages"]) == {"index.html", "special.htm"}


def test_cli_build_reports_build_errors(tmp_path, monkeypatch):
    (tmp_path / "emptyapp.py").write_text(
        textwrap.dedent(
            """
            from jinja2 import Environment

            environment = Environment()
            """
        )
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))

    result = CliRunner().invoke(cli, ["build", "emptyapp:environment"])

    assert result.exit_code != 0
    assert "not registered" in result.output
