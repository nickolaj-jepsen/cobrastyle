import json
import posixpath
import re

import pytest

from cobrastyle_lightningcss import (
    LIGHTNINGCSS_VERSION,
    CssModuleReference,
    Dependency,
    TransformError,
    bundle,
    transform,
)


class DictProvider:
    def __init__(self, files: dict[str, str]):
        self.files = files
        self.reads: list[str] = []

    def read(self, path: str) -> str:
        self.reads.append(path)
        return self.files[path]

    def resolve(self, specifier: str, from_path: str) -> str:
        return posixpath.normpath(posixpath.join(posixpath.dirname(from_path), specifier))


def _remove_whitespace(text: str) -> str:
    return "".join(text.split())


def test_transform():
    css = """
    .hello-world {
      color: red;
    }
    """
    result = transform(filename="test.css", code=css)
    assert _remove_whitespace(result.code) == _remove_whitespace(css)
    assert result.exports is None


def test_transform_minify():
    css = """
    .hello-world {
      color: red
    }
    """
    result = transform(filename="test.css", code=css, minify=True)
    assert result.code == ".hello-world{color:red}"


def test_module():
    css = """
    .hello-world {
      color: red;
    }
    """
    result = transform(
        filename="filename.css", code=css, module=True, module_pattern="test-[name]-[local]", minify=True
    )
    assert result.code == ".test-filename-hello-world{color:red}"
    assert result.exports is not None
    assert result.exports["hello-world"].name == "test-filename-hello-world"


def test_module_composes():
    css = """
    .base { color: black; }
    .button { composes: base; background: red; }
    """
    result = transform(filename="test.css", code=css, module=True, module_pattern="[local]")
    assert result.exports is not None
    (reference,) = result.exports["button"].composes
    assert isinstance(reference, CssModuleReference.Local)
    assert reference.name == "base"


def test_targets_adds_vendor_prefixes():
    css = ".a { user-select: none; }"
    result = transform(filename="test.css", code=css, minify=True, targets=["safari >= 13"])
    assert "-webkit-user-select" in result.code


def test_parse_error():
    # An invalid selector is a hard parse error (invalid declarations are just skipped)
    with pytest.raises(TransformError, match="parse"):
        transform(filename="test.css", code="..a { color: red; }")
    assert issubclass(TransformError, ValueError)


def test_parse_error_location_is_one_based():
    with pytest.raises(TransformError, match=r"at test\.css:3:2") as excinfo:
        transform(filename="test.css", code="\n\n..a { color: red; }")

    assert excinfo.value.filename == "test.css"
    assert excinfo.value.line == 3
    assert excinfo.value.column == 2


def test_error_without_location_has_none_attributes():
    with pytest.raises(TransformError) as excinfo:
        transform(filename="test.css", code=".a {}", targets=["not a real browser query %%"])

    assert excinfo.value.filename is None
    assert excinfo.value.line is None
    assert excinfo.value.column is None


def test_classes_report_their_module():
    result = transform(filename="test.css", code=".a { color: red; }")

    assert type(result).__module__ == "cobrastyle_lightningcss"
    assert "TransformResult" in repr(result)
    assert "color: red" in repr(result)


def test_invalid_module_pattern():
    with pytest.raises(TransformError, match="pattern"):
        transform(filename="test.css", code=".a {}", module=True, module_pattern="[unknown]")


def test_invalid_targets():
    with pytest.raises(TransformError, match="targets"):
        transform(filename="test.css", code=".a {}", targets=["not a real browser query %%"])


def test_lightningcss_version_is_exposed():
    assert re.match(r"^\d+\.\d+\.\d+", LIGHTNINGCSS_VERSION)


def test_analyze_dependencies_off_by_default():
    result = transform(filename="test.css", code=".a { background: url(icon.svg); }")
    assert result.dependencies is None
    assert "icon.svg" in result.code


def test_analyze_dependencies_url():
    result = transform(filename="test.css", code=".a { background: url(img/icon.svg); }", analyze_dependencies=True)

    assert result.dependencies is not None
    (dependency,) = result.dependencies
    assert isinstance(dependency, Dependency.Url)
    assert dependency.url == "img/icon.svg"
    assert dependency.placeholder in result.code
    assert "img/icon.svg" not in result.code
    assert dependency.loc.file_path == "test.css"


def test_analyze_dependencies_import():
    result = transform(filename="test.css", code='@import "other.css"; .a { color: red; }', analyze_dependencies=True)

    assert result.dependencies is not None
    (dependency,) = result.dependencies
    assert isinstance(dependency, Dependency.Import)
    assert dependency.url == "other.css"
    assert dependency.placeholder in result.code


def test_remove_imports():
    result = transform(
        filename="test.css",
        code='@import "other.css"; .a { color: red; }',
        analyze_dependencies=True,
        remove_imports=True,
    )

    assert "@import" not in result.code
    assert result.dependencies is not None
    (dependency,) = result.dependencies
    assert isinstance(dependency, Dependency.Import)
    assert dependency.url == "other.css"


def test_transform_source_map():
    result = transform(filename="src/test.css", code=".a { color: red; }", minify=True, source_map=True)

    assert result.map is not None
    map_data = json.loads(result.map)
    assert "src/test.css" in map_data["sources"]
    assert map_data["sourcesContent"] == [".a { color: red; }"]
    assert map_data["mappings"]


def test_bundle_inlines_imports():
    provider = DictProvider(
        {
            "entry.css": '@import "sub/base.css"; .entry { color: red; }',
            "sub/base.css": '@import "./deep.css"; .base { color: blue; }',
            "sub/deep.css": ".deep { color: green; }",
        }
    )
    result = bundle(filename="entry.css", provider=provider, minify=True)

    assert "@import" not in result.code
    # Import order preserved: deepest first
    assert result.code.index(".deep") < result.code.index(".base") < result.code.index(".entry")
    assert result.files == ["entry.css", "sub/base.css", "sub/deep.css"]


def test_bundle_without_imports_matches_transform():
    css = ".a { color: red; }"
    bundled = bundle(filename="test.css", provider=DictProvider({"test.css": css}), minify=True)
    transformed = transform(filename="test.css", code=css, minify=True)

    assert bundled.code == transformed.code
    assert bundled.files == ["test.css"]


def test_bundle_media_condition_is_preserved():
    provider = DictProvider(
        {
            "entry.css": '@import "print.css" print; .entry { color: red; }',
            "print.css": ".page { margin: 0; }",
        }
    )
    result = bundle(filename="entry.css", provider=provider, minify=True)

    assert "@media print" in result.code
    assert ".page" in result.code


def test_bundle_supports_condition_is_preserved():
    provider = DictProvider(
        {
            "entry.css": '@import "grid.css" supports(display: grid); .entry { color: red; }',
            "grid.css": ".grid { display: grid; }",
        }
    )
    result = bundle(filename="entry.css", provider=provider, minify=True)

    assert "@supports (display:grid)" in result.code


def test_bundle_layer_import():
    provider = DictProvider(
        {
            "entry.css": '@import "theme.css" layer(theme); .entry { color: red; }',
            "theme.css": ".themed { color: teal; }",
        }
    )
    result = bundle(filename="entry.css", provider=provider, minify=True)

    assert "@layer theme" in result.code
    assert ".themed" in result.code


def test_bundle_deduplicates_shared_imports():
    provider = DictProvider(
        {
            "entry.css": '@import "a.css"; @import "b.css";',
            "a.css": '@import "shared.css"; .a { color: red; }',
            "b.css": '@import "shared.css"; .b { color: blue; }',
            "shared.css": ".shared { color: green; }",
        }
    )
    result = bundle(filename="entry.css", provider=provider, minify=True)

    assert result.code.count(".shared") == 1
    assert result.files == ["a.css", "b.css", "entry.css", "shared.css"]


def test_bundle_module_exports_only_the_entry():
    provider = DictProvider(
        {
            "entry.css": '@import "other.css"; .entry { color: red; }',
            "other.css": ".other { color: blue; }",
        }
    )
    result = bundle(filename="entry.css", provider=provider, module=True, minify=True)

    assert result.exports is not None
    assert set(result.exports) == {"entry"}
    # The imported file's classes are still module-scoped in the output
    assert ".other{" not in result.code


def test_bundle_module_class_hashes_match_transform():
    css = ".entry { color: red; }"
    bundled = bundle(filename="pages/entry.css", provider=DictProvider({"pages/entry.css": css}), module=True)
    transformed = transform(filename="pages/entry.css", code=css, module=True)

    assert bundled.exports is not None
    assert transformed.exports is not None
    assert bundled.exports["entry"].name == transformed.exports["entry"].name


def test_bundle_keeps_external_imports():
    provider = DictProvider({"entry.css": '@import "https://example.com/reset.css"; .entry { color: red; }'})
    result = bundle(filename="entry.css", provider=provider, minify=True)

    assert "https://example.com/reset.css" in result.code
    assert result.files == ["entry.css"]


def test_bundle_external_import_becomes_dependency_under_analysis():
    provider = DictProvider(
        {"entry.css": '@import "https://example.com/reset.css"; .entry { background: url(icon.svg); }'}
    )
    result = bundle(filename="entry.css", provider=provider, minify=True, analyze_dependencies=True)

    assert result.dependencies is not None
    kinds = {type(dependency) for dependency in result.dependencies}
    assert kinds == {Dependency.Import, Dependency.Url}


def test_bundle_url_dependency_reports_the_importing_file():
    provider = DictProvider(
        {
            "entry.css": '@import "sub/icons.css"; .entry { color: red; }',
            "sub/icons.css": ".icon { background: url(icon.svg); }",
        }
    )
    result = bundle(filename="entry.css", provider=provider, minify=True, analyze_dependencies=True)

    assert result.dependencies is not None
    (dependency,) = result.dependencies
    assert isinstance(dependency, Dependency.Url)
    assert dependency.loc.file_path == "sub/icons.css"


def test_bundle_source_map_covers_all_sources():
    provider = DictProvider(
        {
            "entry.css": '@import "other.css"; .entry { color: red; }',
            "other.css": ".other { color: blue; }",
        }
    )
    result = bundle(filename="entry.css", provider=provider, minify=True, source_map=True)

    assert result.map is not None
    map_data = json.loads(result.map)
    assert set(map_data["sources"]) == {"entry.css", "other.css"}
    assert len(map_data["sourcesContent"]) == 2


def test_bundle_missing_import_propagates_the_provider_error():
    provider = DictProvider({"entry.css": '@import "missing.css";'})

    with pytest.raises(KeyError, match=r"missing\.css"):
        bundle(filename="entry.css", provider=provider)


def test_bundle_provider_exception_propagates_unchanged():
    class ExplodingProvider(DictProvider):
        def read(self, path: str) -> str:
            raise OSError(f"cannot read {path}")

    with pytest.raises(OSError, match=r"cannot read entry\.css") as excinfo:
        bundle(filename="entry.css", provider=ExplodingProvider({}))
    assert type(excinfo.value) is OSError


def test_bundle_provider_base_exception_escapes():
    class InterruptingProvider(DictProvider):
        def read(self, path: str) -> str:
            raise KeyboardInterrupt

    def bundle_catching_ordinary_exceptions():
        # Must not be swallowed into a catchable TransformError: except Exception can't eat it
        try:
            bundle(filename="entry.css", provider=InterruptingProvider({}))
        except Exception as exc:
            raise AssertionError(f"provider BaseException was downgraded to {type(exc).__name__}") from exc

    with pytest.raises(KeyboardInterrupt):
        bundle_catching_ordinary_exceptions()


def test_bundle_circular_import_is_deduplicated():
    provider = DictProvider(
        {
            "a.css": '@import "b.css"; .a { color: red; }',
            "b.css": '@import "a.css"; .b { color: blue; }',
        }
    )
    result = bundle(filename="a.css", provider=provider, minify=True)

    assert result.code.count(".a") == 1
    assert result.code.count(".b") == 1
    assert result.files == ["a.css", "b.css"]


def test_module_composes_global():
    result = transform(
        filename="test.css", code=".x { composes: gname from global; }", module=True, module_pattern="[local]"
    )

    assert result.exports is not None
    (reference,) = result.exports["x"].composes
    assert isinstance(reference, CssModuleReference.Global)
    assert reference.name == "gname"


def test_module_composes_dependency():
    result = transform(
        filename="button.css",
        code='.button { composes: base from "./base.css"; }',
        module=True,
        module_pattern="[local]",
    )

    assert result.exports is not None
    (reference,) = result.exports["button"].composes
    assert isinstance(reference, CssModuleReference.Dependency)
    assert reference.name == "base"
    assert reference.specifier == "./base.css"


def test_transform_source_map_without_minify():
    result = transform(filename="test.css", code=".a { color: red; }", source_map=True)

    assert result.map is not None
    assert json.loads(result.map)["mappings"]


def test_bundle_targets_adds_vendor_prefixes():
    provider = DictProvider({"entry.css": ".a { user-select: none; }"})
    result = bundle(filename="entry.css", provider=provider, minify=True, targets=["safari >= 13"])

    assert "-webkit-user-select" in result.code


def test_bundle_module_pattern():
    provider = DictProvider({"entry.css": ".a { color: red; }"})
    result = bundle(filename="entry.css", provider=provider, module=True, module_pattern="x-[local]")

    assert result.exports is not None
    assert result.exports["a"].name == "x-a"


def test_bundle_keeps_protocol_relative_and_fragment_imports():
    provider = DictProvider(
        {"entry.css": '@import "//example.com/reset.css"; @import "#anchor"; .entry { color: red; }'}
    )
    result = bundle(filename="entry.css", provider=provider, minify=True)

    assert "//example.com/reset.css" in result.code
    assert "#anchor" in result.code
    assert result.files == ["entry.css"]


def test_bundle_provider_resolve_exception_propagates_unchanged():
    class BadResolve(DictProvider):
        def resolve(self, specifier: str, from_path: str) -> str:
            raise ValueError(f"cannot resolve {specifier}")

    provider = BadResolve({"entry.css": '@import "other.css";', "other.css": ""})

    with pytest.raises(ValueError, match=r"cannot resolve other\.css") as excinfo:
        bundle(filename="entry.css", provider=provider)
    assert type(excinfo.value) is ValueError  # the original, not a TransformError wrapper
