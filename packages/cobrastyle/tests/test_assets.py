"""Asset pipeline: cross-file composes, url() assets, dev asset serving."""

import pytest
from jinja2 import Environment, FileSystemLoader

from cobrastyle import CobrastyleManager, FileSystemResolver, InMemoryResolver
from cobrastyle.build import BuildError, build
from cobrastyle.jinja2 import CobrastyleExtension, configure
from cobrastyle.manifest import Manifest
from cobrastyle.serve import get_resource


def test_composes_from_other_file():
    resolver = InMemoryResolver(
        {
            "base.css": ".base { color: black; }",
            "button.css": '.button { composes: base from "./base.css"; background: red; }',
        }
    )
    manager = CobrastyleManager(resolver, module_pattern="[local]")

    stylesheet = manager.import_module("button.css")

    assert stylesheet.classes["button"] == "button base"
    assert stylesheet.composes == ("base.css",)
    assert manager.get("base.css") is not None


def test_composes_transitive():
    resolver = InMemoryResolver(
        {
            "reset.css": ".reset { margin: 0; }",
            "base.css": '.base { composes: reset from "./reset.css"; color: black; }',
            "button.css": '.button { composes: base from "./base.css"; }',
        }
    )
    manager = CobrastyleManager(resolver, module_pattern="[local]")

    stylesheet = manager.import_module("button.css")

    assert stylesheet.classes["button"] == "button base reset"
    assert stylesheet.composes == ("reset.css", "base.css")


def test_composes_cycle_is_an_error():
    resolver = InMemoryResolver(
        {
            "a.css": '.a { composes: b from "./b.css"; }',
            "b.css": '.b { composes: a from "./a.css"; }',
        }
    )
    manager = CobrastyleManager(resolver, module_pattern="[local]")

    with pytest.raises(ValueError, match="Circular"):
        manager.import_module("a.css")


def test_composes_missing_class_is_an_error():
    resolver = InMemoryResolver(
        {
            "base.css": ".base { color: black; }",
            "button.css": '.button { composes: nope from "./base.css"; }',
        }
    )
    manager = CobrastyleManager(resolver, module_pattern="[local]")

    with pytest.raises(KeyError, match="nope"):
        manager.import_module("button.css")


def test_links_include_composed_modules():
    from jinja2 import DictLoader

    environment = Environment(
        loader=DictLoader(
            {
                "page.html": '{% cobrastyle styles = "button.css" %}{{ cobrastyle.links() }}',
            }
        ),
        extensions=[CobrastyleExtension],
    )
    configure(
        environment,
        resolver=InMemoryResolver(
            {
                "base.css": ".base { color: black; }",
                "button.css": '.button { composes: base from "./base.css"; }',
            }
        ),
        module_pattern="[local]",
    )

    html = environment.get_template("page.html").render()

    # Base first, so derived rules win the cascade
    assert html == ('<link rel="stylesheet" href="base.css" /><link rel="stylesheet" href="button.css" />')


@pytest.fixture
def asset_project(tmp_path):
    templates = tmp_path / "templates"
    styles = tmp_path / "styles"
    templates.mkdir()
    (styles / "img").mkdir(parents=True)

    (styles / "img" / "icon.svg").write_bytes(b"<svg></svg>")
    (styles / "button.css").write_text(
        ".button { background: url(img/icon.svg); cursor: url(img/icon.svg#frag); }"
        ".ext { background: url(https://example.com/x.png); }"
    )
    (templates / "index.html").write_text('{% cobrastyle styles = "button.css" %}{{ cobrastyle.links() }}')
    return tmp_path


def make_environment(project) -> Environment:
    environment = Environment(loader=FileSystemLoader(project / "templates"), extensions=[CobrastyleExtension])
    configure(environment, resolver=FileSystemResolver(project / "styles"))
    return environment


def test_build_hashes_and_rewrites_assets(asset_project):
    out = asset_project / "out"
    manifest = build(make_environment(asset_project), output_dir=out, url_prefix="/static/")

    asset = manifest.assets["img/icon.svg"]
    assert (out / asset.file).read_bytes() == b"<svg></svg>"
    assert asset.url == "/static/" + asset.file

    entry = manifest.modules["button.css"]
    assert entry.assets == ["img/icon.svg"]
    built = (out / entry.file).read_text()
    assert asset.url in built
    assert f"{asset.url}#frag" in built
    assert "https://example.com/x.png" in built
    # No placeholders left behind
    assert "img/icon.svg" not in built

    reloaded = Manifest.load(out / "manifest.json")
    assert reloaded.assets == manifest.assets


def test_build_is_deterministic_with_assets(asset_project):
    one, two = asset_project / "one", asset_project / "two"
    build(make_environment(asset_project), output_dir=one)
    build(make_environment(asset_project), output_dir=two)

    first = {p.relative_to(one): p.read_bytes() for p in one.rglob("*") if p.is_file()}
    second = {p.relative_to(two): p.read_bytes() for p in two.rglob("*") if p.is_file()}
    assert first == second


def test_build_missing_asset_fails(asset_project):
    (asset_project / "styles" / "button.css").write_text(".button { background: url(img/missing.png); }")

    with pytest.raises(BuildError, match=r"missing\.png"):
        build(make_environment(asset_project), output_dir=asset_project / "out")


def test_build_rejects_import_between_modules(asset_project):
    (asset_project / "styles" / "other.css").write_text(".other { color: red; }")
    (asset_project / "styles" / "button.css").write_text('@import "other.css"; .button { color: blue; }')

    with pytest.raises(BuildError, match="@import"):
        build(make_environment(asset_project), output_dir=asset_project / "out")


def test_dev_serves_raw_assets(asset_project):
    manager = CobrastyleManager(FileSystemResolver(asset_project / "styles"))

    resource = get_resource(manager, "img/icon.svg")
    assert resource is not None
    assert resource.body == b"<svg></svg>"
    assert resource.content_type == "image/svg+xml"

    assert get_resource(manager, "img/missing.svg") is None
    assert get_resource(manager, "../secret.txt") is None


def test_dev_css_keeps_relative_urls(asset_project):
    manager = CobrastyleManager(FileSystemResolver(asset_project / "styles"))

    resource = get_resource(manager, "button.css")
    assert resource is not None
    assert b"url(img/icon.svg)" in resource.body or b'url("img/icon.svg")' in resource.body
