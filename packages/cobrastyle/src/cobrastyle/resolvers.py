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
    def resolve(self, path: str) -> ResolvedFile:
        """Return the content of ``path``, the URL it will be served from, and a freshness token."""
        ...

    def mtime(self, path: str) -> float | None:
        """Return a cheap freshness token for ``path``, or None if the source never changes."""
        ...

    def read_bytes(self, path: str) -> bytes:
        """Return the raw bytes of ``path``."""
        ...


class InMemoryResolver:
    """Resolves stylesheets from an in-memory mapping. Mostly useful for testing."""

    def __init__(self, resources: dict[str, str]):
        self.resources = resources

    def resolve(self, path: str) -> ResolvedFile:
        return ResolvedFile(url=path, content=self.resources[path])

    def mtime(self, path: str) -> float | None:
        return None

    def read_bytes(self, path: str) -> bytes:
        return self.resources[path].encode()


class FileSystemResolver:
    """Resolves stylesheets from files under ``root``, served from ``url_prefix``."""

    def __init__(self, root: str | Path, url_prefix: str = "/cobrastyle/"):
        self.root = Path(root).resolve()
        self.url_prefix = url_prefix if url_prefix.endswith("/") else url_prefix + "/"

    def _resolved(self, path: str) -> Path:
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise StylesheetPathError(f"Stylesheet path escapes the resolver root: {path!r}")
        return resolved

    def resolve(self, path: str) -> ResolvedFile:
        file = self._resolved(path)
        return ResolvedFile(
            url=self.url_prefix + path,
            content=file.read_text(encoding="utf-8"),
            mtime=file.stat().st_mtime,
        )

    def mtime(self, path: str) -> float | None:
        return self._resolved(path).stat().st_mtime

    def read_bytes(self, path: str) -> bytes:
        return self._resolved(path).read_bytes()
