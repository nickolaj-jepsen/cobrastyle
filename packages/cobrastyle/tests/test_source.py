import pytest

from cobrastyle import CobrastyleManager, InMemoryResolver
from cobrastyle.manifest import Manifest, ModuleEntry
from cobrastyle.source import StylesheetNotFoundError, StyleSource


def make_manifest() -> Manifest:
    return Manifest(
        modules={"page.css": ModuleEntry(file="page.abc.css", url="/static/page.abc.css", classes={"title": "t"})},
        pages={"index.html": ["page.css"]},
    )


def test_requires_exactly_one_mode():
    manager = CobrastyleManager(InMemoryResolver({}))

    with pytest.raises(TypeError, match="exactly one"):
        StyleSource()
    with pytest.raises(TypeError, match="exactly one"):
        StyleSource(manager=manager, manifest=make_manifest())


def test_url_map_requires_manifest_mode():
    manager = CobrastyleManager(InMemoryResolver({}))

    with pytest.raises(TypeError, match="url_map"):
        StyleSource(manager=manager, url_map=lambda entry: entry.url)


def test_url_for_missing_manifest_entry_names_the_rebuild_hint():
    source = StyleSource(manifest=make_manifest(), rebuild_hint="make css")

    assert source.url_for("page.css") == "/static/page.abc.css"
    with pytest.raises(StylesheetNotFoundError, match="make css"):
        source.url_for("missing.css")


def test_url_for_compiles_once_then_uses_the_cache():
    manager = CobrastyleManager(InMemoryResolver({"page.css": ".t { color: red; }"}))
    source = StyleSource(manager=manager)

    assert source.url_for("page.css") == "page.css"
    assert manager.get("page.css") is not None
    assert source.url_for("page.css") == "page.css"


def test_manifest_pages_fallback():
    source = StyleSource(manifest=make_manifest())

    assert source.manifest_pages("index.html") == ["page.css"]
    assert source.manifest_pages("unknown.html") is None
    assert source.manifest_pages(None) is None

    dev = StyleSource(manager=CobrastyleManager(InMemoryResolver({})))
    assert dev.manifest_pages("index.html") is None
