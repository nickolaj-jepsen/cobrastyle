import pytest

from cobrastyle import FileSystemResolver, InMemoryResolver


def test_in_memory_resolver():
    resolver = InMemoryResolver({"test.css": ".a {}"})
    resolved = resolver.resolve("test.css")

    assert resolved.url == "test.css"
    assert resolved.content == ".a {}"


def test_file_system_resolver(tmp_path):
    (tmp_path / "styles").mkdir()
    (tmp_path / "styles" / "test.css").write_text(".a {}")
    resolver = FileSystemResolver(tmp_path / "styles", url_prefix="/static/")

    resolved = resolver.resolve("test.css")

    assert resolved.url == "/static/test.css"
    assert resolved.content == ".a {}"


def test_file_system_resolver_rejects_path_traversal(tmp_path):
    (tmp_path / "styles").mkdir()
    (tmp_path / "secret.css").write_text(".a {}")
    resolver = FileSystemResolver(tmp_path / "styles")

    with pytest.raises(ValueError, match="escapes"):
        resolver.resolve("../secret.css")
