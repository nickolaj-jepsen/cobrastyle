from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from django.conf import settings
from django.template import Origin, TemplateSyntaxError

from cobrastyle.manifest import Manifest
from cobrastyle.source import StylesheetNotFoundError, StyleSource

if TYPE_CHECKING:
    from cobrastyle.manager import CobrastyleManager


class DTLRuntime:
    """DTL-side state: an engine-agnostic :class:`StyleSource` plus the
    parse-time page registry keyed by template origin."""

    def __init__(self, manager: CobrastyleManager | None = None, manifest: Manifest | None = None):
        self.source = StyleSource(manager=manager, manifest=manifest, rebuild_hint="manage.py cobrastyle_build")
        # origin.name (absolute) → module paths / relative template name
        self.pages: dict[str, list[str]] = {}
        self.page_names: dict[str, str] = {}

    @property
    def manager(self) -> CobrastyleManager | None:
        return self.source.manager

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

    def record(self, origin: Origin | None, paths: tuple[str, ...]) -> None:
        """Register the module paths a template imports, at parse time."""
        if origin is None:
            return
        page = self.pages.setdefault(origin.name, [])
        if isinstance(origin.template_name, str) and origin.template_name:
            self.page_names[origin.name] = origin.template_name
        for path in paths:
            if path not in page:
                page.append(path)

    def page_modules(self, origin: Origin | None) -> list[str]:
        """Module paths for the template at ``origin``: this process's parse-time
        registry, falling back to the manifest for cached templates parsed elsewhere."""
        if origin is None:
            return []
        paths = self.pages.get(origin.name)
        if paths is None:
            name = origin.template_name
            paths = self.source.manifest_pages(name if isinstance(name, str) else None)
        return list(paths or ())


_lock = threading.Lock()
_runtime: DTLRuntime | None = None


def get_runtime() -> DTLRuntime:
    """The settings-driven runtime singleton (see ``COBRASTYLE`` in cobrastyle.django)."""
    global _runtime
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
    from cobrastyle.django import app_config, common_options, dev_resolver, output_dir

    config = app_config()
    if config.get("DEV", settings.DEBUG):
        from cobrastyle.manager import CobrastyleManager

        return DTLRuntime(manager=CobrastyleManager(dev_resolver(config), **common_options(config)))
    manifest = config.get("MANIFEST", output_dir(config) / "manifest.json")
    if not isinstance(manifest, Manifest):
        manifest = Manifest.load(manifest)
    return DTLRuntime(manifest=manifest)
