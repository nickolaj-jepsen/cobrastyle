from pathlib import Path
from typing import NamedTuple, Protocol, runtime_checkable

from cobrastyle.errors import StylesheetPathError


class ResolvedFile(NamedTuple):
    url: str
    content: str
    mtime: float | None = None


@runtime_checkable
class HasUrlPrefix(Protocol):
    """A resolver that knows the URL prefix its stylesheets are served from.

    Optional: framework adapters mount the dev CSS server at this prefix when
    a resolver provides it, falling back to their own ``url_prefix`` argument.
    """

    url_prefix: str


class FileResolver(Protocol):
    def resolve(self, filename: str) -> ResolvedFile:
        """Return the content of ``filename``, the URL it will be served from, and a freshness token."""
        ...

    def mtime(self, filename: str) -> float | None:
        """Return a cheap freshness token for ``filename``, or None if the source never changes."""
        ...

    def read_bytes(self, filename: str) -> bytes:
        """Return the raw bytes of ``filename``."""
        ...


class InMemoryResolver:
    """Resolves stylesheets from an in-memory mapping. Mostly useful for testing."""

    def __init__(self, resources: dict[str, str]):
        self.resources = resources

    def resolve(self, filename: str) -> ResolvedFile:
        return ResolvedFile(url=filename, content=self.resources[filename])

    def mtime(self, filename: str) -> float | None:
        return None

    def read_bytes(self, filename: str) -> bytes:
        return self.resources[filename].encode()


class FileSystemResolver:
    """Resolves stylesheets from files under ``root``, served from ``url_prefix``."""

    def __init__(self, root: str | Path, url_prefix: str = "/static/"):
        self.root = Path(root).resolve()
        self.url_prefix = url_prefix

    def _path(self, filename: str) -> Path:
        path = (self.root / filename).resolve()
        if not path.is_relative_to(self.root):
            raise StylesheetPathError(f"Stylesheet path escapes the resolver root: {filename!r}")
        return path

    def resolve(self, filename: str) -> ResolvedFile:
        path = self._path(filename)
        return ResolvedFile(
            url=self.url_prefix + filename,
            content=path.read_text(encoding="utf-8"),
            mtime=path.stat().st_mtime,
        )

    def mtime(self, filename: str) -> float | None:
        return self._path(filename).stat().st_mtime

    def read_bytes(self, filename: str) -> bytes:
        return self._path(filename).read_bytes()
