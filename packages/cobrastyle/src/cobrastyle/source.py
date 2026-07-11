from __future__ import annotations

from typing import TYPE_CHECKING

from cobrastyle.manifest import Manifest

if TYPE_CHECKING:
    from cobrastyle.manager import CobrastyleManager


class StylesheetNotFoundError(LookupError):
    """A module path has no entry in the manifest; the message names the rebuild command."""


class StyleSource:
    """Engine-agnostic dev/prod resolution: a compiling manager (dev) or a prebuilt manifest (prod).

    Template integrations (the Jinja2 extension, the DTL runtime) delegate
    here and only translate :class:`StylesheetNotFoundError` into their
    engine's error type.
    """

    def __init__(
        self,
        *,
        manager: CobrastyleManager | None = None,
        manifest: Manifest | None = None,
        rebuild_hint: str = "cobrastyle build",
    ):
        if (manager is None) == (manifest is None):
            raise TypeError("Pass exactly one of manager= (dev) or manifest= (prod)")
        self.manager = manager
        self.manifest = manifest
        self.rebuild_hint = rebuild_hint

    def resolve(self, path: str) -> tuple[dict[str, str], tuple[str, ...]]:
        """Return (class map, module paths the page must link) for ``path``."""
        if self.manifest is not None:
            entry = self.manifest.modules.get(path)
            if entry is None:
                raise StylesheetNotFoundError(
                    f"Stylesheet {path!r} is not in the manifest — did you run `{self.rebuild_hint}` after adding it?"
                )
            return entry.classes, (path,)
        assert self.manager is not None
        stylesheet = self.manager.import_module(path)
        # Composed-from modules first, so their CSS is linked before this module's
        return stylesheet.classes, (*stylesheet.composes, path)

    def url_for(self, path: str) -> str:
        """Return the URL the module at ``path`` is served from."""
        if self.manifest is not None:
            entry = self.manifest.modules.get(path)
            if entry is None:
                raise StylesheetNotFoundError(
                    f"Stylesheet {path!r} is not in the manifest — rebuild with `{self.rebuild_hint}`."
                )
            return entry.url
        assert self.manager is not None
        # URLs are path-derived and render-invariant — skip import_module's freshness stat
        cached = self.manager.get(path)
        return (cached or self.manager.import_module(path)).url

    def manifest_pages(self, page_name: str | None) -> list[str] | None:
        """The manifest's module list for a page whose parse-time registry is empty in this process."""
        if self.manifest is None or not page_name:
            return None
        return self.manifest.pages.get(page_name)
