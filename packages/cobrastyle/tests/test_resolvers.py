import os

import pytest

from cobrastyle import FileSystemResolver, InMemoryResolver


def test_in_memory_resolver():
    resolver = InMemoryResolver({"test.css": ".a {}"})
    resolved = resolver.resolve("test.css")

    assert resolved.url == "test.css"
    assert resolved.content == ".a {}"


def test_in_memory_resolver_freshness_and_bytes():
    resolver = InMemoryResolver({"test.css": ".a {}"})

    assert resolver.mtime("test.css") is None
    assert resolver.read_bytes("test.css") == b".a {}"
    with pytest.raises(KeyError):
        resolver.read_bytes("missing.css")


def test_file_system_resolver_freshness_and_bytes(tmp_path):
    (tmp_path / "test.css").write_text(".a {}")
    os.utime(tmp_path / "test.css", (1000, 1000))
    resolver = FileSystemResolver(tmp_path)

    assert resolver.mtime("test.css") == 1000
    assert resolver.read_bytes("test.css") == b".a {}"


def test_file_system_resolver(tmp_path):
    (tmp_path / "styles").mkdir()
    (tmp_path / "styles" / "test.css").write_text(".a {}")
    resolver = FileSystemResolver(tmp_path / "styles", url_prefix="/static/")

    resolved = resolver.resolve("test.css")

    assert resolved.url == "/static/test.css"
    assert resolved.content == ".a {}"


def test_file_system_resolver_normalizes_a_slashless_url_prefix(tmp_path):
    (tmp_path / "a.css").write_text(".a {}")
    resolver = FileSystemResolver(tmp_path, url_prefix="/styles")

    assert resolver.url_prefix == "/styles/"
    assert resolver.resolve("a.css").url == "/styles/a.css"


def test_file_system_resolver_rejects_path_traversal(tmp_path):
    (tmp_path / "styles").mkdir()
    (tmp_path / "secret.css").write_text(".a {}")
    resolver = FileSystemResolver(tmp_path / "styles")

    for access in (resolver.resolve, resolver.mtime, resolver.read_bytes):
        with pytest.raises(ValueError, match="escapes"):
            access("../secret.css")
