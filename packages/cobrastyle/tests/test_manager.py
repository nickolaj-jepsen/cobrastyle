from cobrastyle import CobrastyleManager, InMemoryResolver, ResolvedFile


class CountingResolver:
    def __init__(self, resources: dict[str, str]):
        self.resources = resources
        self.calls = 0

    def resolve(self, filename: str) -> ResolvedFile:
        self.calls += 1
        return ResolvedFile(url=filename, content=self.resources[filename])


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
