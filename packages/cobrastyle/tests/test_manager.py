import os
import re
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from cobrastyle import (
    CircularComposesError,
    CobrastyleManager,
    ComposesExportError,
    FileSystemResolver,
    InMemoryResolver,
    ResolvedFile,
    StylesheetNotFoundError,
)
from cobrastyle_lightningcss import CssModuleReference


class CountingResolver:
    def __init__(self, resources: dict[str, str]):
        self.resources = resources
        self.calls = 0

    def resolve(self, filename: str) -> ResolvedFile:
        self.calls += 1
        return ResolvedFile(url=filename, content=self.resources[filename])

    def mtime(self, filename: str) -> float | None:
        return None

    def read_bytes(self, filename: str) -> bytes:
        return self.resources[filename].encode()


def test_import_module():
    manager = CobrastyleManager(
        InMemoryResolver({"test.css": ".header { color: red; }"}), module_pattern="[local]", minify=True
    )
    stylesheet = manager.import_module("test.css")

    assert stylesheet.path == "test.css"
    assert stylesheet.url == "test.css"
    assert stylesheet.classes == {"header": "header"}
    assert stylesheet.code == ".header{color:red}"


def test_dev_defaults_are_debuggable():
    manager = CobrastyleManager(InMemoryResolver({"test.css": ".header { color: red; }"}), module_pattern="[local]")
    stylesheet = manager.import_module("test.css")

    assert stylesheet.code.count("\n") > 0  # not minified
    assert stylesheet.map is not None


def test_default_class_names_are_readable():
    manager = CobrastyleManager(InMemoryResolver({"components/button.css": ".primary { color: red; }"}))
    classes = manager.import_module("components/button.css").classes

    assert re.fullmatch(r"button_primary_[\w-]+", classes["primary"])


def test_default_class_names_disambiguate_same_stem():
    css = ".primary { color: red; }"
    manager = CobrastyleManager(InMemoryResolver({"a/button.css": css, "b/button.css": css}))

    first = manager.import_module("a/button.css").classes["primary"]
    second = manager.import_module("b/button.css").classes["primary"]

    assert first != second  # the [hash] covers the path, not just the stem


def test_imports_are_bundled():
    manager = CobrastyleManager(
        InMemoryResolver(
            {
                "entry.css": '@import "theme.css"; .entry { color: red; }',
                "theme.css": ".themed { color: teal; }",
            }
        ),
        module_pattern="[local]",
        minify=True,
    )

    stylesheet = manager.import_module("entry.css")

    assert "@import" not in stylesheet.code
    assert stylesheet.code.index(".themed") < stylesheet.code.index(".entry")
    assert dict(stylesheet.dep_mtimes).keys() == {"entry.css", "theme.css"}


def test_imported_file_edit_recompiles(tmp_path):
    (tmp_path / "entry.css").write_text('@import "theme.css"; .entry { color: red; }')
    theme = tmp_path / "theme.css"
    theme.write_text(".themed { color: teal; }")
    for file in tmp_path.iterdir():
        os.utime(file, (1000, 1000))
    manager = CobrastyleManager(FileSystemResolver(tmp_path), minify=True)

    first = manager.import_module("entry.css")
    assert manager.import_module("entry.css") is first

    theme.write_text(".themed { color: navy; }")
    os.utime(theme, (2000, 2000))
    second = manager.import_module("entry.css")

    assert second is not first
    assert "navy" in second.code


def test_composed_file_edit_recompiles(tmp_path):
    (tmp_path / "button.css").write_text('.button { composes: base from "./base.css"; }')
    base = tmp_path / "base.css"
    base.write_text(".base { color: black; }")
    for file in tmp_path.iterdir():
        os.utime(file, (1000, 1000))
    manager = CobrastyleManager(FileSystemResolver(tmp_path), minify=True)

    first = manager.import_module("button.css")
    assert manager.import_module("button.css") is first

    base.write_text(".base { color: gray; }")
    os.utime(base, (2000, 2000))
    second = manager.import_module("button.css")

    assert second is not first
    assert "gray" in second.code


def test_deleted_dependency_recompiles_with_error(tmp_path):
    (tmp_path / "entry.css").write_text('@import "theme.css"; .entry { color: red; }')
    (tmp_path / "theme.css").write_text(".themed { color: teal; }")
    manager = CobrastyleManager(FileSystemResolver(tmp_path))

    manager.import_module("entry.css")
    (tmp_path / "theme.css").unlink()

    with pytest.raises(Exception, match=r"theme\.css"):
        manager.import_module("entry.css")


def test_import_module_is_cached():
    resolver = CountingResolver({"test.css": ".header { color: red; }"})
    manager = CobrastyleManager(resolver)

    first = manager.import_module("test.css")
    second = manager.import_module("test.css")

    assert first is second
    assert resolver.calls == 1


def test_class_name_rewrite_does_not_clobber_existing():
    css = ".hello-world { color: red; } .hello_world { color: blue; }"
    manager = CobrastyleManager(InMemoryResolver({"test.css": css}), module_pattern="[local]")
    stylesheet = manager.import_module("test.css")

    assert stylesheet.classes["hello-world"] == "hello-world"
    assert stylesheet.classes["hello_world"] == "hello_world"


def test_composes_local():
    css = ".base { color: black; } .button { composes: base; background: red; }"
    manager = CobrastyleManager(InMemoryResolver({"test.css": css}), module_pattern="[local]")
    stylesheet = manager.import_module("test.css")

    assert stylesheet.classes["button"] == "button base"


def test_stylesheets():
    resources = {
        "one.css": ".one { color: red; }",
        "two.css": ".two { color: blue; }",
    }
    manager = CobrastyleManager(InMemoryResolver(resources))
    manager.import_module("one.css")
    manager.import_module("two.css")

    assert [stylesheet.path for stylesheet in manager.stylesheets] == ["one.css", "two.css"]
    assert manager.get("one.css") is not None
    assert manager.get("missing.css") is None


def test_mtime_edit_recompiles(tmp_path):
    css = tmp_path / "test.css"
    css.write_text(".a { color: red; }")
    os.utime(css, (1000, 1000))
    manager = CobrastyleManager(FileSystemResolver(tmp_path), minify=True)

    first = manager.import_module("test.css")
    assert manager.import_module("test.css") is first

    css.write_text(".a { color: blue; }")
    os.utime(css, (2000, 2000))
    second = manager.import_module("test.css")

    assert second is not first
    assert "#00f" in second.code
    # Class names hash the path, not the content — the old map stays valid
    assert second.classes == first.classes


def test_class_names_stable_across_locations(tmp_path):
    for name in ("one", "two"):
        root = tmp_path / name
        root.mkdir()
        (root / "test.css").write_text(".a { color: red; }")

    classes = [
        CobrastyleManager(FileSystemResolver(tmp_path / name)).import_module("test.css").classes
        for name in ("one", "two")
    ]

    assert classes[0] == classes[1]


def test_path_normalization():
    manager = CobrastyleManager(CountingResolver({"test.css": ".a {}"}))

    assert manager.import_module("./test.css") is manager.import_module("test.css")
    assert manager.get("./test.css") is not None

    with pytest.raises(ValueError, match="relative"):
        manager.import_module("/etc/passwd")
    with pytest.raises(ValueError, match="relative"):
        manager.import_module("../secret.css")


def test_targets_add_vendor_prefixes():
    manager = CobrastyleManager(
        InMemoryResolver({"test.css": ".a { user-select: none; }"}), targets=["safari >= 13"], minify=True
    )

    assert "-webkit-user-select" in manager.import_module("test.css").code


def test_source_map_can_be_disabled():
    manager = CobrastyleManager(InMemoryResolver({"test.css": ".a { color: red; }"}), source_map=False)

    assert manager.import_module("test.css").map is None


def fake_bundle(modules):
    """A bundle() stand-in that keeps cross-file composes as Dependency references.

    lightningcss's bundler resolves composes-from-file itself (see test_assets),
    so the manager's recursive Dependency linking can only be exercised this way.
    """

    def bundle(*, filename, provider, **kwargs):
        code, exports = modules[filename]
        return SimpleNamespace(
            code=code,
            exports={name: SimpleNamespace(name=name, composes=list(refs)) for name, refs in exports.items()},
            dependencies=None,
            map=None,
            files=[filename],
        )

    return bundle


def composes_from(name, specifier):
    return CssModuleReference.Dependency(name=name, specifier=specifier)


def use_fake_bundle(monkeypatch, modules) -> CobrastyleManager:
    monkeypatch.setattr("cobrastyle.manager.bundle", fake_bundle(modules))
    return CobrastyleManager(InMemoryResolver(dict.fromkeys(modules, "")))


def test_dependency_reference_imports_the_composed_module(monkeypatch):
    manager = use_fake_bundle(
        monkeypatch,
        {
            "button.css": (".button {}", {"button": [composes_from("base", "./base.css")]}),
            "base.css": (".base {}", {"base": []}),
        },
    )

    stylesheet = manager.import_module("button.css")

    assert stylesheet.classes["button"] == "button base"
    assert stylesheet.composes == ("base.css",)
    assert "base.css" in dict(stylesheet.dep_mtimes)
    assert manager.get("base.css") is not None


def test_dependency_reference_links_transitive_composes_base_first(monkeypatch):
    manager = use_fake_bundle(
        monkeypatch,
        {
            "button.css": (".button {}", {"button": [composes_from("base", "./base.css")]}),
            "base.css": (".base {}", {"base": [composes_from("reset", "./reset.css")]}),
            "reset.css": (".reset {}", {"reset": []}),
        },
    )

    stylesheet = manager.import_module("button.css")

    assert stylesheet.classes["button"] == "button base reset"
    assert stylesheet.composes == ("reset.css", "base.css")


def test_dependency_reference_to_missing_export_raises(monkeypatch):
    manager = use_fake_bundle(
        monkeypatch,
        {
            "button.css": (".button {}", {"button": [composes_from("nope", "./base.css")]}),
            "base.css": (".base {}", {"base": []}),
        },
    )

    with pytest.raises(ComposesExportError, match="does not export a class named 'nope'"):
        manager.import_module("button.css")


def test_circular_composes_chain_raises(monkeypatch):
    manager = use_fake_bundle(
        monkeypatch,
        {
            "a.css": (".a {}", {"a": [composes_from("b", "./b.css")]}),
            "b.css": (".b {}", {"b": [composes_from("a", "./a.css")]}),
        },
    )

    with pytest.raises(CircularComposesError, match="Circular composes chain"):
        manager.import_module("a.css")


def test_circular_composes_message_shows_the_actual_chain(monkeypatch):
    manager = use_fake_bundle(
        monkeypatch,
        {
            "page.css": (".page {}", {"page": [composes_from("b", "./b.css")]}),
            "b.css": (".b {}", {"b": [composes_from("a", "./a.css")]}),
            "a.css": (".a {}", {"a": [composes_from("b", "./b.css")]}),
        },
    )

    with pytest.raises(CircularComposesError, match=r"b\.css -> a\.css -> b\.css$"):
        manager.import_module("page.css")


def test_missing_module_raises_stylesheet_not_found():
    manager = CobrastyleManager(InMemoryResolver({}))

    with pytest.raises(StylesheetNotFoundError, match=r"'missing\.css' not found") as excinfo:
        manager.import_module("missing.css")
    assert excinfo.value.path == "missing.css"


def test_concurrent_imports_compile_once():
    resolver = CountingResolver({"test.css": ".a { color: red; }"})
    manager = CobrastyleManager(resolver)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: manager.import_module("test.css"), range(64)))

    assert resolver.calls == 1
    assert all(result is results[0] for result in results)


def test_dev_pattern_sanitizes_whitespace_in_the_file_stem():
    manager = CobrastyleManager(InMemoryResolver({"button primary.css": ".title { color: red }"}))

    classes = manager.import_module("button primary.css").classes

    # class attributes are whitespace-delimited; a raw stem would split the name in two
    assert classes["title"].startswith("button_primary_title_")
