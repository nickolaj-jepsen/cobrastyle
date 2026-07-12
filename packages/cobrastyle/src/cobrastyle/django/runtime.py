from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from django.template import Origin, TemplateSyntaxError
from django.utils.safestring import SafeString, mark_safe

from cobrastyle.errors import StylesheetNotFoundError
from cobrastyle.manifest import Manifest, ModuleEntry
from cobrastyle.source import StyleSource

if TYPE_CHECKING:
    from collections.abc import Callable

    from cobrastyle.manager import CobrastyleManager


@dataclass
class PageRecord:
    """One parsed template's cobrastyle modules, plus the relative name the manifest keys them by.

    Origins carry an absolute path; the manifest carries the loader-relative
    name. Both live here so they cannot fall out of sync.
    """

    template_name: str | None = None
    paths: list[str] = field(default_factory=list)


class DTLRuntime:
    """DTL-side state: an engine-agnostic :class:`StyleSource` plus the
    parse-time page registry keyed by template origin."""

    def __init__(
        self,
        manager: CobrastyleManager | None = None,
        manifest: Manifest | None = None,
        url_map: Callable[[ModuleEntry], str] | None = None,
        hot_reload_prefix: str | None = None,
    ):
        self.source = StyleSource(
            manager=manager,
            manifest=manifest,
            url_map=url_map,
            hot_reload_prefix=hot_reload_prefix,
            markup=mark_safe,
            rebuild_hint="manage.py cobrastyle_build",
        )
        # origin.name (absolute) → what the template imported, keyed for the manifest
        self.pages: dict[str, PageRecord] = {}

    @property
    def manager(self) -> CobrastyleManager | None:
        return self.source.manager

    @property
    def hot_reload_prefix(self) -> str | None:
        return self.source.hot_reload_prefix

    def resolve(self, path: str) -> tuple[dict[str, str], tuple[str, ...]]:
        """Return (class map, module paths the page must link) for ``path``."""
        try:
            return self.source.resolve(path)
        except StylesheetNotFoundError as exc:
            raise TemplateSyntaxError(str(exc)) from exc

    def url_for(self, path: str) -> str:
        try:
            return self.source.url_for(path)
        except StylesheetNotFoundError as exc:
            raise TemplateSyntaxError(str(exc)) from exc

    def links_html(self, paths: list[str]) -> SafeString:
        """``<link>`` markup for ``paths`` (memoized in prod; hot-reload script in dev)."""
        try:
            # Already safe: the source marks (and caches) it with markup=mark_safe
            return cast("SafeString", self.source.links_html(paths))
        except StylesheetNotFoundError as exc:
            raise TemplateSyntaxError(str(exc)) from exc

    def fragment_links_html(self, paths: list[str], *, nonce: str | None = None) -> SafeString:
        """Fragment-response markup (HTMX out-of-band swap) loading ``paths``."""
        try:
            return cast("SafeString", self.source.fragment_links_html(paths, nonce=nonce))
        except StylesheetNotFoundError as exc:
            raise TemplateSyntaxError(str(exc)) from exc

    def record(self, origin: Origin | None, paths: tuple[str, ...]) -> None:
        """Register the module paths a template imports, at parse time."""
        if origin is None:
            return
        page = self.pages.setdefault(origin.name, PageRecord())
        if isinstance(origin.template_name, str) and origin.template_name:
            page.template_name = origin.template_name
        for path in paths:
            if path not in page.paths:
                page.paths.append(path)

    def page_modules(self, origin: Origin | None) -> list[str]:
        """Module paths for the template at ``origin``: this process's parse-time
        registry, falling back to the manifest for cached templates parsed elsewhere."""
        if origin is None:
            return []
        page = self.pages.get(origin.name)
        if page is not None:
            return list(page.paths)
        name = origin.template_name
        return list(self.source.manifest_pages(name if isinstance(name, str) else None) or ())


_lock = threading.Lock()
_runtime: DTLRuntime | None = None


def get_runtime() -> DTLRuntime:
    """The settings-driven runtime singleton (see ``COBRASTYLE`` in cobrastyle.django)."""
    global _runtime
    # Lock-free fast path: this sits on every {% cobrastyle_links %} render, and a
    # fully-constructed runtime is published atomically by the assignment below.
    runtime = _runtime
    if runtime is not None:
        return runtime
    with _lock:
        if _runtime is None:
            _runtime = _create()
        return _runtime


def set_runtime(runtime: DTLRuntime | None) -> None:
    """Replace (or with None, drop) the singleton; the next access recreates it from settings."""
    global _runtime
    with _lock:
        _runtime = runtime


def _create() -> DTLRuntime:
    from cobrastyle.django import (
        app_config,
        common_options,
        dev_resolver,
        hot_reload_enabled,
        is_dev,
        manifest_source,
        static_url_map,
    )

    config = app_config()
    if is_dev(config):
        from cobrastyle.manager import CobrastyleManager

        resolver = dev_resolver(config)
        hot_reload_prefix = resolver.url_prefix if hot_reload_enabled(config) else None
        return DTLRuntime(
            manager=CobrastyleManager(resolver, **common_options(config)),
            hot_reload_prefix=hot_reload_prefix,
        )
    manifest = manifest_source(config)
    if not isinstance(manifest, Manifest):
        manifest = Manifest.load(manifest)
    return DTLRuntime(manifest=manifest, url_map=static_url_map(config))
