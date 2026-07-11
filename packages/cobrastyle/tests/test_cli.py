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


def test_adapt_rejects_factory_returning_factory():
    with pytest.raises(click.ClickException, match="another callable"):
        adapt_target(lambda: lambda: make_environment())


def test_import_target_requires_colon():
    with pytest.raises(click.UsageError, match=r"package\.module:attribute"):
        import_target("just_a_module")


def test_import_target_missing_attribute():
    with pytest.raises(click.ClickException, match="no attribute"):
        import_target("jinja2:does_not_exist")


def test_cli_build_end_to_end(tmp_path, monkeypatch):
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

    result = CliRunner().invoke(
        cli, ["build", "buildapp:create_environment", "--out", "dist", "--url-prefix", "/assets/"]
    )

    assert result.exit_code == 0, result.output
    assert "1 module(s)" in result.output

    manifest = json.loads((tmp_path / "dist" / "manifest.json").read_text())
    entry = manifest["modules"]["page.css"]
    assert entry["url"] == "/assets/" + entry["file"]
    assert (tmp_path / "dist" / entry["file"]).exists()
    assert manifest["pages"] == {"index.html": ["page.css"]}


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
