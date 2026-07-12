import json
import re

import pytest
from jinja2 import DictLoader, Environment, FileSystemLoader, FunctionLoader

from cobrastyle import FileSystemResolver, InMemoryResolver
from cobrastyle.build import BuildError, build, collect_jinja2, emit
from cobrastyle.jinja2 import CobrastyleExtension, configure
from cobrastyle.manager import Stylesheet
from cobrastyle.manifest import Manifest
from cobrastyle_lightningcss import transform


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


def test_manifest_pages_carry_the_whole_template_graph(project):
    """A page's entry is its closure: what it inherits and includes, not only its own tags."""
    (project / "templates" / "_partial.html").write_text('{% cobrastyle c = "styles/card.css" %}{{ c.card }}')
    (project / "templates" / "base.html").write_text(
        '{% cobrastyle b = "styles/button.css" %}{{ cobrastyle.links() }}{% block content %}{% endblock %}'
    )
    (project / "templates" / "index.html").write_text(
        '{% extends "base.html" %}{% block content %}{% cobrastyle styles = "styles/page.css" %}'
        '{% include "_partial.html" %}{% endblock %}'
    )

    manifest = build(make_environment(project), output_dir=project / "dist")

    assert manifest.pages["index.html"] == ["styles/button.css", "styles/page.css", "styles/card.css"]
    assert manifest.pages["_partial.html"] == ["styles/card.css"]


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


@pytest.mark.parametrize(
    ("specifier", "external"),
    [
        ("https://cdn.example.com/a.css", True),
        ("HTTPS://cdn.example.com/a.css", True),
        ("data:text/css,.a{}", True),
        ("custom+scheme.x:y", True),
        ("//cdn.example.com/a.css", True),
        ("#fragment", True),
        ("./theme.css", False),
        ("theme.css", False),
        ("sub/theme.css", False),
        ("1:not-a-scheme.css", False),
    ],
)
def test_external_url_classification_matches_the_bundler(specifier, external):
    """build.py's _EXTERNAL_URL and the Rust bundler's is_external must agree (see both comments)."""
    from cobrastyle.build import _EXTERNAL_URL
    from cobrastyle_lightningcss import bundle

    assert bool(_EXTERNAL_URL.match(specifier)) is external

    reads: list[str] = []

    class Provider:
        def read(self, path: str) -> str:
            reads.append(path)
            return f'@import "{specifier}";' if path == "entry.css" else ".x {}"

        def resolve(self, s: str, f: str) -> str:
            return "resolved.css"

        def is_global(self, path: str) -> bool:
            return False

    bundle(filename="entry.css", provider=Provider())
    bundler_external = "resolved.css" not in reads
    assert bundler_external is external


def test_build_fails_on_codegen_errors():
    # Unknown filters surface at code generation, after parse: the walk must still catch them
    source = '{% cobrastyle styles = "test.css" %}{{ styles.a | no_such_filter }}'
    environment = Environment(loader=DictLoader({"page.html": source}), extensions=[CobrastyleExtension])
    configure(environment, resolver=InMemoryResolver({"test.css": ".a { color: red; }"}))

    with pytest.raises(BuildError, match="no_such_filter"):
        collect_jinja2(environment)


def test_default_globs_cover_the_jinja2_suffix():
    source = '{% cobrastyle styles = "test.css" %}{{ styles.a }}'
    environment = Environment(loader=DictLoader({"page.jinja2": source}), extensions=[CobrastyleExtension])
    configure(environment, resolver=InMemoryResolver({"test.css": ".a { color: red; }"}))

    collected = collect_jinja2(environment)

    assert collected.pages == {"page.jinja2": ["test.css"]}


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


def test_url_prefix_without_trailing_slash_is_normalized(project):
    manifest = build(make_environment(project), output_dir=project / "out", url_prefix="/assets")

    entry = manifest.modules["styles/page.css"]
    assert entry.url == "/assets/" + entry.file


def test_collect_requires_the_extension():
    environment = Environment(loader=DictLoader({}))

    with pytest.raises(BuildError, match="not registered"):
        collect_jinja2(environment)


def test_build_requires_a_loader():
    environment = Environment(extensions=[CobrastyleExtension])
    configure(environment, resolver=InMemoryResolver({}))

    with pytest.raises(BuildError, match="no loader"):
        build(environment, output_dir="/unused")


def test_unloadable_template_fails(project):
    with pytest.raises(BuildError, match="Cannot load template"):
        build(make_environment(project), output_dir=project / "out", extra_templates=("missing.html",))


def test_build_with_no_templates_emits_an_empty_manifest(tmp_path):
    environment = Environment(loader=DictLoader({}), extensions=[CobrastyleExtension])
    configure(environment, resolver=InMemoryResolver({}))

    # clean=True on a directory that does not exist yet is a no-op
    manifest = build(environment, output_dir=tmp_path / "out", clean=True)

    assert manifest.modules == {}
    assert manifest.pages == {}
    assert (tmp_path / "out" / "manifest.json").exists()


def test_generator_version_falls_back_to_unknown(project, monkeypatch):
    from importlib.metadata import PackageNotFoundError

    def missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr("cobrastyle.build.package_version", missing)

    manifest = build(make_environment(project), output_dir=project / "out")

    assert manifest.generator["cobrastyle"] == "unknown"


def mint_dependency(filename, css):
    """Compile ``css`` with dependency analysis to get a real Dependency object and its placeholder code."""
    result = transform(filename=filename, code=css, analyze_dependencies=True)
    assert result.dependencies is not None
    (dependency,) = result.dependencies
    return result.code, dependency


def test_emit_rejects_unresolved_local_imports(tmp_path):
    # The bundler inlines every non-external @import before emit sees it; a
    # relative Import dependency reaching emit means the pipeline broke
    code, dependency = mint_dependency("page.css", '@import "local.css"; .a { color: red; }')
    stylesheet = Stylesheet(path="page.css", url="page.css", code=code, classes={}, dependencies=(dependency,))

    with pytest.raises(BuildError, match="Unresolvable @import"):
        emit([stylesheet], None, {}, output_dir=tmp_path / "out")


def test_emit_rejects_assets_escaping_the_root(tmp_path):
    code, dependency = mint_dependency("page.css", ".a { background: url(../../evil.svg); }")
    stylesheet = Stylesheet(path="page.css", url="page.css", code=code, classes={}, dependencies=(dependency,))

    with pytest.raises(BuildError, match="escapes the resolver root"):
        emit([stylesheet], None, {}, output_dir=tmp_path / "out")


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


def test_build_rejects_manifest_configured_environment(tmp_path):
    environment = Environment(
        loader=DictLoader({"index.html": '{% cobrastyle styles = "page.css" %}{{ styles.title }}'}),
        extensions=[CobrastyleExtension],
    )
    manifest = Manifest.from_dict(
        {"version": 1, "modules": {"page.css": {"file": "page.x.css", "url": "/static/page.x.css", "classes": {}}}}
    )
    configure(environment, manifest=manifest)

    # Nothing compiles in manifest mode; silently emitting a module-less manifest would break prod
    with pytest.raises(BuildError, match="manifest"):
        build(environment, output_dir=tmp_path / "out")


def test_build_carries_a_global_stylesheet(project, tmp_path):
    (project / "styles" / "styles" / "reset.global.css").write_text(".container { margin: 0 auto; }")
    (project / "templates" / "index.html").write_text(
        '{% extends "base.html" %}{% cobrastyle "styles/reset.global.css" %}'
        '{% block content %}{% cobrastyle styles = "styles/page.css" %}'
        '<h1 class="{{ styles.title }}">Hi</h1>{% endblock %}'
    )
    environment = make_environment(project)
    out = project / "out"

    manifest = build(environment, output_dir=out, url_prefix="/static/cobrastyle/")

    entry = manifest.modules["styles/reset.global.css"]
    assert entry.classes == {}  # a global exports nothing, but is still built and linked
    assert manifest.pages["index.html"] == ["styles/reset.global.css", "styles/page.css"]
    built = (out / entry.file).read_text()
    assert built == ".container{margin:0 auto}"  # unscoped, in the built output
