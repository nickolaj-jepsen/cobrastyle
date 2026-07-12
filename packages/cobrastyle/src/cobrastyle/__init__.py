from typing import TYPE_CHECKING

from cobrastyle.cx import cx
from cobrastyle.errors import (
    BuildError,
    CircularComposesError,
    CobrastyleError,
    ComposesExportError,
    ManifestError,
    StylesheetDecodeError,
    StylesheetNotFoundError,
    StylesheetPathError,
)
from cobrastyle.resolvers import FileResolver, FileSystemResolver, InMemoryResolver, ResolvedFile

if TYPE_CHECKING:
    from cobrastyle.manager import CobrastyleManager, Stylesheet

__all__ = [
    "BuildError",
    "CircularComposesError",
    "CobrastyleError",
    "CobrastyleManager",
    "ComposesExportError",
    "FileResolver",
    "FileSystemResolver",
    "InMemoryResolver",
    "ManifestError",
    "ResolvedFile",
    "Stylesheet",
    "StylesheetDecodeError",
    "StylesheetNotFoundError",
    "StylesheetPathError",
    "cx",
]


def __getattr__(name: str) -> object:
    # Lazy so the prod path (manifest mode) never imports the compiler wheel.
    if name in ("CobrastyleManager", "Stylesheet"):
        from cobrastyle import manager

        return getattr(manager, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
