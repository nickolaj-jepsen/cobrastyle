import json
import re

import pytest
from jinja2 import Environment, FileSystemLoader, FunctionLoader

from cobrastyle import FileSystemResolver, InMemoryResolver
from cobrastyle.build import BuildError, build
from cobrastyle.jinja2 import CobrastyleExtension, configure
from cobrastyle.manifest import Manifest


@pytest.fixture
def project(tmp_path):
    templates = tmp_path / "templates"
    styles = tmp_path / "styles"
    (templates / "shop").mkdir(parents=True)
    (styles / "styles").mkdir(parents=True)

    (styles / "styles" / "page.css").write_text(".title { color: red; }")
    (styles / "styles" / "button.css").write_text(".button { color: blue; }")
    (styles / "styles" / "card.css").write_text(".card { padding: 1rem; }")

    (templates / "base.html").write_text(
        "<head>{{ cobrastyle.links() }}</head><body>{% block content %}{% endblock %}</body>"
    )
    (templates / "index.html").write_text(
        '{% extends "base.html" %}{% block content %}{% cobrastyle styles = "styles/page.css" %}'
        '<h1 class="{{ styles.title }}">Hi</h1>{% endblock %}'
    )
    (templates / "shop" / "product.html").write_text(
        '{% cobrastyle b = "styles/button.css" %}{% cobrastyle c = "styles/card.css" %}'
        "{{ cobrastyle.links() }}{{ b.button }}{{ c.card }}"
    )
    (templates / "plain.html").write_text("<p>No styles here</p>")
    (templates / "notes.txt").write_text("not a template")
    return tmp_path


def make_environment(project, **configure_options) -> Environment:
    environment = Environment(loader=FileSystemLoader(project / "templates"), extensions=[CobrastyleExtension])
    configure(environment, resolver=FileSystemResolver(project / "styles"), **configure_options)
    return environment


def test_build_writes_hashed_css_and_manifest(project):
    environment = make_environment(project)
    out = project / "out"

    manifest = build(environment, output_dir=out, url_prefix="/static/cobrastyle/")

    assert set(manifest.modules) == {"styles/page.css", "styles/button.css", "styles/card.css"}
    assert manifest.pages == {
        "index.html": ["styles/page.css"],
        "shop/product.html": ["styles/button.css", "styles/card.css"],
    }

    entry = manifest.modules["styles/button.css"]
    assert entry.url == "/static/cobrastyle/" + entry.file
    built = (out / entry.file).read_text()
    class_name = entry.classes["button"]
    assert f".{class_name}" in built

    on_disk = Manifest.load(out / "manifest.json")
    assert on_disk == manifest
    assert on_disk.generator["cobrastyle"]


def test_build_defaults_to_compact_class_names(project):
    manifest = build(make_environment(project), output_dir=project / "out")

    class_name = manifest.modules["styles/button.css"].classes["button"]
    assert re.fullmatch(r"[\w-]+_button", class_name)
    assert not class_name.startswith("button_")  # not the readable dev pattern


def test_build_honors_explicit_module_pattern(project):
    manifest = build(make_environment(project, module_pattern="[local]"), output_dir=project / "out")

    assert manifest.modules["styles/button.css"].classes["button"] == "button"


def test_build_is_deterministic(project):
    first_dir, second_dir = project / "one", project / "two"
    build(make_environment(project), output_dir=first_dir)
    build(make_environment(project), output_dir=second_dir)

    first = {path.relative_to(first_dir): path.read_bytes() for path in first_dir.rglob("*") if path.is_file()}
    second = {path.relative_to(second_dir): path.read_bytes() for path in second_dir.rglob("*") if path.is_file()}
    assert first == second


def test_build_minifies_regardless_of_dev_configuration(project):
    environment = make_environment(project, minify=False)
    out = project / "out"

    manifest = build(environment, output_dir=out)

    built = (out / manifest.modules["styles/page.css"].file).read_text()
    assert "color:red" in built
    assert "sourceMappingURL" not in built


def test_build_no_minify(project):
    out = project / "out"

    manifest = build(make_environment(project), output_dir=out, minify=False)

    built = (out / manifest.modules["styles/page.css"].file).read_text()
    assert "color: red" in built


def test_rebuild_without_clean_accumulates_stale_files(project):
    out = project / "out"
    build(make_environment(project), output_dir=out)
    stale = next((out / "styles").glob("page.*.css"))

    (project / "styles" / "styles" / "page.css").write_text(".title { color: green; }")
    build(make_environment(project), output_dir=out)

    assert stale.exists()


def test_clean_removes_only_previously_built_files(project):
    out = project / "out"
    build(make_environment(project), output_dir=out)
    stale = next((out / "styles").glob("page.*.css"))
    keep = out / "logo.png"
    keep.write_bytes(b"not ours")
    (out / "empty").mkdir()
    (out / "empty" / "old.0123456789.css").write_text("stale")

    (project / "styles" / "styles" / "page.css").write_text(".title { color: green; }")
    manifest = build(make_environment(project), output_dir=out, clean=True)

    assert not stale.exists()
    assert not (out / "empty").exists()
    assert keep.read_bytes() == b"not ours"
    fresh = out / manifest.modules["styles/page.css"].file
    assert "green" in fresh.read_text()


def test_broken_template_without_cobrastyle_is_skipped(project, caplog):
    (project / "templates" / "broken.html").write_text("{% block %}")
    environment = make_environment(project)

    with caplog.at_level("WARNING", logger="cobrastyle.build"):
        manifest = build(environment, output_dir=project / "out")

    assert "broken.html" not in manifest.pages
    assert any("broken.html" in record.message for record in caplog.records)


def test_broken_template_with_cobrastyle_fails(project):
    (project / "templates" / "broken.html").write_text('{% cobrastyle styles = "styles/missing.css" %}')

    with pytest.raises(BuildError, match=r"broken\.html"):
        build(make_environment(project), output_dir=project / "out")


def test_strict_fails_on_any_broken_template(project):
    (project / "templates" / "broken.html").write_text("{% block %}")

    with pytest.raises(BuildError, match=r"broken\.html"):
        build(make_environment(project), output_dir=project / "out", strict=True)


def test_globs_filter_templates(project):
    manifest = build(make_environment(project), output_dir=project / "out", globs=("shop/*.html",))

    assert set(manifest.pages) == {"shop/product.html"}
    assert set(manifest.modules) == {"styles/button.css", "styles/card.css"}


def test_function_loader_requires_extra_templates():
    source = '{% cobrastyle styles = "test.css" %}{{ styles.a }}'
    environment = Environment(loader=FunctionLoader(lambda name: source), extensions=[CobrastyleExtension])
    configure(environment, resolver=InMemoryResolver({"test.css": ".a { color: red; }"}))

    with pytest.raises(BuildError, match="extra_templates"):
        build(environment, output_dir="/tmp/unused")


def test_function_loader_with_extra_templates(project):
    source = '{% cobrastyle styles = "test.css" %}{{ styles.a }}'
    environment = Environment(loader=FunctionLoader(lambda name: source), extensions=[CobrastyleExtension])
    configure(environment, resolver=InMemoryResolver({"test.css": ".a { color: red; }"}))

    manifest = build(environment, output_dir=project / "out", extra_templates=("page.html",))

    assert manifest.pages == {"page.html": ["test.css"]}
    assert set(manifest.modules) == {"test.css"}


def test_build_does_not_pollute_app_environment(project):
    environment = make_environment(project)
    build(environment, output_dir=project / "out")

    html = environment.get_template("index.html").render()
    assert "<h1" in html


def test_manifest_json_is_valid_schema(project):
    out = project / "out"
    build(make_environment(project), output_dir=out)

    data = json.loads((out / "manifest.json").read_text())
    assert data["version"] == 1
    assert set(data) == {"version", "generator", "modules", "assets", "pages"}
