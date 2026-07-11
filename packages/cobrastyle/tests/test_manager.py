import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from cobrastyle import CobrastyleManager, FileSystemResolver, InMemoryResolver, ResolvedFile


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
    manager = CobrastyleManager(InMemoryResolver({"test.css": ".header { color: red; }"}), module_pattern="[local]")
    stylesheet = manager.import_module("test.css")

    assert stylesheet.path == "test.css"
    assert stylesheet.url == "test.css"
    assert stylesheet.classes == {"header": "header"}
    assert stylesheet.code == ".header{color:red}"


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


def test_concurrent_imports_compile_once():
    resolver = CountingResolver({"test.css": ".a { color: red; }"})
    manager = CobrastyleManager(resolver)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: manager.import_module("test.css"), range(64)))

    assert resolver.calls == 1
    assert all(result is results[0] for result in results)
