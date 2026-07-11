from pathlib import Path
from typing import NamedTuple, Protocol


class ResolvedFile(NamedTuple):
    url: str
    content: str


class FileResolver(Protocol):
    def resolve(self, filename: str) -> ResolvedFile:
        """Return the content of ``filename`` and the URL it will be served from."""
        ...


class InMemoryResolver:
    """Resolves stylesheets from an in-memory mapping. Mostly useful for testing."""

    def __init__(self, resources: dict[str, str]):
        self.resources = resources

    def resolve(self, filename: str) -> ResolvedFile:
        return ResolvedFile(url=filename, content=self.resources[filename])


class FileSystemResolver:
    """Resolves stylesheets from files under ``root``, served from ``url_prefix``."""

    def __init__(self, root: str | Path, url_prefix: str = "/static/"):
        self.root = Path(root).resolve()
        self.url_prefix = url_prefix

    def resolve(self, filename: str) -> ResolvedFile:
        path = (self.root / filename).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"Stylesheet path escapes the resolver root: {filename!r}")
        return ResolvedFile(url=self.url_prefix + filename, content=path.read_text())
