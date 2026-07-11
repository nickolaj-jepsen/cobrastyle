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
    manager = CobrastyleManager(resolver, module_pattern="[local]", minify=True)

    stylesheet = manager.import_module("button.css")

    assert stylesheet.classes["button"] == "button base"
    # The composed-from module is bundled in, its rules first so the composer wins the cascade
    assert stylesheet.code.index(".base") < stylesheet.code.index(".button")
    assert stylesheet.composes == ()
    assert manager.get("base.css") is None
    assert dict(stylesheet.dep_mtimes).keys() == {"base.css", "button.css"}


def test_composes_transitive():
    resolver = InMemoryResolver(
        {
            "reset.css": ".reset { margin: 0; }",
            "base.css": '.base { composes: reset from "./reset.css"; color: black; }',
            "button.css": '.button { composes: base from "./base.css"; }',
        }
    )
    manager = CobrastyleManager(resolver, module_pattern="[local]", minify=True)

    stylesheet = manager.import_module("button.css")

    assert stylesheet.classes["button"] == "button base reset"
    assert dict(stylesheet.dep_mtimes).keys() == {"reset.css", "base.css", "button.css"}
    code = stylesheet.code
    assert code.index(".reset") < code.index(".base") < code.index(".button")


def test_composes_cycle_bundles_each_module_once():
    resolver = InMemoryResolver(
        {
            "a.css": '.a { composes: b from "./b.css"; color: red; }',
            "b.css": '.b { composes: a from "./a.css"; color: blue; }',
        }
    )
    manager = CobrastyleManager(resolver, module_pattern="[local]", minify=True)

    stylesheet = manager.import_module("a.css")

    assert stylesheet.code.count(".a") == 1
    assert stylesheet.code.count(".b") == 1


def test_composes_missing_class_is_dropped():
    # lightningcss's bundler resolves cross-file composes itself and silently
    # drops references to classes the target does not export
    resolver = InMemoryResolver(
        {
            "base.css": ".base { color: black; }",
            "button.css": '.button { composes: nope from "./base.css"; }',
        }
    )
    manager = CobrastyleManager(resolver, module_pattern="[local]")

    assert manager.import_module("button.css").classes["button"] == "button"


def test_composed_modules_are_bundled_not_linked():
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

    # base.css's rules ride along inside button.css; only the page's module is linked
    assert html == '<link rel="stylesheet" href="button.css" />'


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


def test_build_bundles_imports_between_modules(asset_project):
    (asset_project / "styles" / "other.css").write_text(".other { color: red; }")
    (asset_project / "styles" / "button.css").write_text('@import "other.css"; .button { color: blue; }')
    out = asset_project / "out"

    manifest = build(make_environment(asset_project), output_dir=out)

    # The imported file is inlined, not emitted as a module of its own
    assert set(manifest.modules) == {"button.css"}
    built = (out / manifest.modules["button.css"].file).read_text()
    assert "@import" not in built
    assert built.index("color:red") < built.index("color:#00f")


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
