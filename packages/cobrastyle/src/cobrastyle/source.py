from __future__ import annotations

from typing import TYPE_CHECKING

from cobrastyle.errors import StylesheetNotFoundError
from cobrastyle.fragments import fragment_links_html
from cobrastyle.manifest import Manifest, ModuleEntry
from cobrastyle.markup import stylesheet_links_html

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

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
        hot_reload_prefix: str | None = None,
        markup: Callable[[str], str] = str,
    ):
        if (manager is None) == (manifest is None):
            raise TypeError("Pass exactly one of manager= (dev) or manifest= (prod)")
        if url_map is not None and manifest is None:
            raise TypeError("url_map= only applies to manifest mode; dev URLs come from the manager")
        if hot_reload_prefix is not None and manifest is not None:
            raise TypeError("hot_reload_prefix= is dev-only; manifest mode serves static, immutable CSS")
        self.manager = manager
        self.manifest = manifest
        self.rebuild_hint = rebuild_hint
        self.url_map = url_map
        # Serving prefix the hot-reload client loads from; None disables the script
        self.hot_reload_prefix = hot_reload_prefix
        # The engine's "this is safe HTML" wrapper (jinja2's Markup, Django's
        # mark_safe). Applied before caching, so a cache hit is a plain lookup.
        self.markup = markup
        # Manifest entries and url_map are immutable per process, and Django's
        # static() url_map is expensive enough to dominate a DTL prod render
        self._urls: dict[str, str] = {}
        # Manifest-mode markup by module tuple — immutable per process, so both
        # engines render a page's <link>s (and a fragment's) once and reuse them
        self._links: dict[tuple[str, ...], str] = {}
        self._fragments: dict[tuple[str, ...], str] = {}

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
        module's entry instead of returning the one baked in at build time;
        either way the URL resolves once per path and is cached.
        """
        if self.manifest is not None:
            url = self._urls.get(path)
            if url is None:
                entry = self._entry(path)
                url = self.url_map(entry) if self.url_map is not None else entry.url
                self._urls[path] = url
            return url
        assert self.manager is not None
        # URLs are path-derived and render-invariant — skip import_module's freshness stat
        cached = self.manager.get(path)
        return (cached or self.manager.import_module(path)).url

    def links_html(self, paths: Sequence[str]) -> str:
        """``<link>`` markup for ``paths``, plus the hot-reload script in dev.

        Engine-safe markup (see ``markup``), memoized in manifest mode.
        """
        if self.manifest is None:
            urls = [self.url_for(path) for path in paths]
            return self.markup(stylesheet_links_html(urls, hot_reload_prefix=self.hot_reload_prefix))
        key = tuple(paths)
        cached = self._links.get(key)
        if cached is None:
            cached = self.markup(stylesheet_links_html([self.url_for(path) for path in key]))
            self._links[key] = cached
        return cached

    def fragment_links_html(self, paths: Sequence[str], *, nonce: str | None = None) -> str:
        """Fragment-response markup (HTMX out-of-band swap) loading ``paths``.

        A per-request ``nonce`` is never memoized — it would grow the cache without bound.
        """
        if self.manifest is None or nonce is not None:
            return self.markup(fragment_links_html([self.url_for(path) for path in paths], nonce=nonce))
        key = tuple(paths)
        cached = self._fragments.get(key)
        if cached is None:
            cached = self.markup(fragment_links_html([self.url_for(path) for path in key]))
            self._fragments[key] = cached
        return cached

    def manifest_pages(self, page_name: str | None) -> list[str] | None:
        """The manifest's module list for a page whose parse-time registry is empty in this process."""
        if self.manifest is None or not page_name:
            return None
        return self.manifest.pages.get(page_name)
