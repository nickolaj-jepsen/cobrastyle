from __future__ import annotations

from typing import TYPE_CHECKING

from cobrastyle.errors import StylesheetNotFoundError
from cobrastyle.manifest import Manifest, ModuleEntry

if TYPE_CHECKING:
    from collections.abc import Callable

    from cobrastyle.manager import CobrastyleManager

__all__ = ["StyleSource", "StylesheetNotFoundError"]


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
        url_map: Callable[[ModuleEntry], str] | None = None,
    ):
        if (manager is None) == (manifest is None):
            raise TypeError("Pass exactly one of manager= (dev) or manifest= (prod)")
        if url_map is not None and manifest is None:
            raise TypeError("url_map= only applies to manifest mode; dev URLs come from the manager")
        self.manager = manager
        self.manifest = manifest
        self.rebuild_hint = rebuild_hint
        self.url_map = url_map

    def _entry(self, path: str) -> ModuleEntry:
        assert self.manifest is not None
        entry = self.manifest.modules.get(path)
        if entry is None:
            raise StylesheetNotFoundError(
                f"Stylesheet {path!r} is not in the manifest — did you run `{self.rebuild_hint}` after adding it?",
                path=path,
            )
        return entry

    def resolve(self, path: str) -> tuple[dict[str, str], tuple[str, ...]]:
        """Return (class map, module paths the page must link) for ``path``.

        Raises StylesheetNotFoundError when ``path`` resolves to nothing in
        the current mode.
        """
        if self.manifest is not None:
            return self._entry(path).classes, (path,)
        assert self.manager is not None
        stylesheet = self.manager.import_module(path)
        # Composed-from modules first, so their CSS is linked before this module's
        return stylesheet.classes, (*stylesheet.composes, path)

    def url_for(self, path: str) -> str:
        """Return the URL the module at ``path`` is served from.

        In manifest mode, ``url_map`` (when set) derives the URL from the
        module's entry instead of returning the one baked in at build time.
        """
        if self.manifest is not None:
            entry = self._entry(path)
            return self.url_map(entry) if self.url_map is not None else entry.url
        assert self.manager is not None
        # URLs are path-derived and render-invariant — skip import_module's freshness stat
        cached = self.manager.get(path)
        return (cached or self.manager.import_module(path)).url

    def manifest_pages(self, page_name: str | None) -> list[str] | None:
        """The manifest's module list for a page whose parse-time registry is empty in this process."""
        if self.manifest is None or not page_name:
            return None
        return self.manifest.pages.get(page_name)
