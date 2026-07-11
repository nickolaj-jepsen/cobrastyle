from __future__ import annotations

import json
import threading
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, cast

from jinja2 import Environment, TemplateSyntaxError, nodes, pass_context
from jinja2.ext import Extension
from jinja2.parser import Parser
from jinja2.runtime import Context
from markupsafe import Markup

from cobrastyle.manifest import Manifest
from cobrastyle.paths import normalize_path
from cobrastyle.resolvers import FileResolver
from cobrastyle.source import StylesheetNotFoundError, StyleSource

if TYPE_CHECKING:
    from cobrastyle.manager import CobrastyleManager

_USED_KEY = "_cobrastyle_used"
_PAGE_GLOBAL = "__cobrastyle_page__"


class ExtendedEnvironment(Environment):
    """Type-only view of an Environment carrying the attributes ``CobrastyleExtension`` adds
    via ``environment.extend()``. Never instantiated — see :func:`extended`."""

    globals: dict[str, Any]
    cobrastyle_resolver: FileResolver | None
    cobrastyle_manifest: Manifest | None
    cobrastyle_minify: bool
    cobrastyle_rewrite_class_names: bool
    cobrastyle_module_pattern: str | None
    cobrastyle_targets: list[str] | None
    cobrastyle_analyze_dependencies: bool


def extended(environment: Environment) -> ExtendedEnvironment:
    """View ``environment`` through the attributes :class:`CobrastyleExtension` extends it with."""
    return cast(ExtendedEnvironment, environment)


def configure(
    environment: Environment,
    *,
    resolver: FileResolver | None = None,
    manifest: Manifest | str | Path | None = None,
    minify: bool = True,
    rewrite_class_names: bool = True,
    module_pattern: str | None = None,
    targets: list[str] | None = None,
) -> None:
    """Configure cobrastyle on an environment using :class:`CobrastyleExtension`.

    Pass exactly one of ``resolver`` (dev mode: stylesheets compile on
    demand) or ``manifest`` (prod mode: class maps and URLs come from a
    prebuilt manifest, the compiler is never imported). Must be called
    before any template using ``{% cobrastyle %}`` is loaded.

    Dev mode rejects a ``bytecode_cache`` — a cache hit would silently skip
    the page tracking behind ``links()``; prod mode is cache-safe.
    """
    if (resolver is None) == (manifest is None):
        raise TypeError("Pass exactly one of resolver= (dev mode) or manifest= (prod mode)")
    if resolver is not None and environment.bytecode_cache is not None:
        raise RuntimeError(
            "cobrastyle dev mode is incompatible with a bytecode_cache: cache hits skip "
            "template compilation, silently breaking cobrastyle.links(). Remove the "
            "bytecode_cache or use a manifest in production."
        )
    if isinstance(manifest, str | Path):
        manifest = Manifest.load(manifest)
    env = extended(environment)
    env.cobrastyle_resolver = resolver
    env.cobrastyle_manifest = manifest
    env.cobrastyle_minify = minify
    env.cobrastyle_rewrite_class_names = rewrite_class_names
    env.cobrastyle_module_pattern = module_pattern
    env.cobrastyle_targets = targets


class CobrastyleExtension(Extension):
    """Jinja2 extension that resolves CSS modules at template compile time.

    Usage::

        {% cobrastyle styles = "button.css" %}
        <button class="{{ styles.button }}">Click me</button>

    and in the layout's ``<head>``: ``{{ cobrastyle.links() }}``.

    In dev mode (``configure(resolver=...)``) modules compile on demand; in
    prod mode (``configure(manifest=...)``) class maps and URLs are looked up
    in the manifest and the compiler is never imported.
    """

    tags: set[str] = {"cobrastyle"}  # noqa: RUF012 — matches jinja2's Extension.tags declaration

    def __init__(self, environment: Environment):
        super().__init__(environment)
        environment.extend(
            cobrastyle_resolver=None,
            cobrastyle_manifest=None,
            cobrastyle_minify=True,
            cobrastyle_rewrite_class_names=True,
            cobrastyle_module_pattern=None,
            cobrastyle_targets=None,
            cobrastyle_analyze_dependencies=False,
        )
        extended(environment).globals["cobrastyle"] = CobrastyleRuntime(self)
        extended(environment).globals[_PAGE_GLOBAL] = self._enter_page
        self._manager: CobrastyleManager | None = None
        # Module paths statically imported by each compiled template
        self._pages: dict[str, list[str]] = {}
        self._anonymous_ids = count()
        self._compiling = threading.local()

    @classmethod
    def get(cls, environment: Environment) -> Self | None:
        """Return this extension's instance registered on ``environment``, if any."""
        extension = environment.extensions.get(cls.identifier)
        return extension if isinstance(extension, cls) else None

    @property
    def manifest(self) -> Manifest | None:
        return extended(self.environment).cobrastyle_manifest

    @property
    def manager(self) -> CobrastyleManager:
        if self._manager is None:
            environment = extended(self.environment)
            if environment.cobrastyle_resolver is None:
                raise RuntimeError(
                    "No stylesheet resolver configured; call cobrastyle.jinja2.configure() "
                    "with resolver= (dev) or manifest= (prod) before loading templates."
                )
            from cobrastyle.manager import CobrastyleManager

            self._manager = CobrastyleManager(
                environment.cobrastyle_resolver,
                minify=environment.cobrastyle_minify,
                module_pattern=environment.cobrastyle_module_pattern,
                rewrite_class_names=environment.cobrastyle_rewrite_class_names,
                targets=environment.cobrastyle_targets,
                analyze_dependencies=environment.cobrastyle_analyze_dependencies,
            )
        return self._manager

    def preprocess(self, source: str, name: str | None, filename: str | None = None) -> str:
        # Either cache layer can skip preprocess/parse — never let a stale page_id leak into the next compile
        self._compiling.page_id = None
        if "cobrastyle" not in source:
            return source
        page_id = name if name is not None else f"<anonymous-{next(self._anonymous_ids)}>"
        self._pages[page_id] = []
        self._compiling.page_id = page_id
        # Prepended without a newline so template line numbers stay intact. The
        # top-level set runs at render start — before any parent template renders —
        # which lets {{ cobrastyle.links() }} in an inherited <head> see this
        # template's stylesheets.
        return f"{{% set __cobrastyle__ = {_PAGE_GLOBAL}({json.dumps(page_id)}) %}}{source}"

    def parse(self, parser: Parser) -> nodes.Node:
        lineno = next(parser.stream).lineno
        target = parser.parse_assign_target()
        parser.stream.expect("assign")
        path_expression = parser.parse_expression()
        if not isinstance(path_expression, nodes.Const) or not isinstance(path_expression.value, str):
            raise TemplateSyntaxError(
                'cobrastyle expects a constant string path, e.g. {% cobrastyle styles = "page.css" %}',
                lineno,
                parser.name,
                parser.filename,
            )

        path = normalize_path(path_expression.value)
        classes, page_paths = self._resolve_module(path, lineno, parser)

        page_id: str | None = getattr(self._compiling, "page_id", None)
        if page_id is not None:
            page = self._pages[page_id]
            for page_path in page_paths:
                if page_path not in page:
                    page.append(page_path)

        return nodes.Assign(target, nodes.Const(classes)).set_lineno(lineno)

    def _resolve_module(self, path: str, lineno: int, parser: Parser) -> tuple[dict[str, str], tuple[str, ...]]:
        try:
            return self.source.resolve(path)
        except StylesheetNotFoundError as exc:
            raise TemplateSyntaxError(str(exc), lineno, parser.name, parser.filename) from exc

    @property
    def source(self) -> StyleSource:
        """The current-mode resolution source; built per access so reconfiguration takes effect."""
        manifest = self.manifest
        if manifest is not None:
            return StyleSource(manifest=manifest)
        return StyleSource(manager=self.manager)

    def stylesheet_url(self, path: str) -> str:
        """Return the URL the module at ``path`` is served from."""
        return self.source.url_for(path)

    def page_modules(self, page_id: str) -> list[str]:
        """Return the module paths statically imported by the template ``page_id``.

        Prefers this process's parse-time collection; falls back to the
        manifest, which is what keeps ``links()`` correct in workers that
        loaded the template from a cache and never compiled it.
        """
        paths = self._pages.get(page_id)
        if paths is None:
            paths = self.source.manifest_pages(page_id)
        return list(paths or ())

    @pass_context
    def _enter_page(self, context: Context, page_id: str) -> str:
        """Record the template's statically imported stylesheets in the render context."""
        used = context.vars.setdefault(_USED_KEY, [])
        for path in self.page_modules(page_id):
            if path not in used:
                used.append(path)
        return ""


class CobrastyleRuntime:
    """The ``cobrastyle`` template global."""

    def __init__(self, extension: CobrastyleExtension):
        self._extension = extension

    @pass_context
    def links(self, context: Context) -> Markup:
        """Render <link> tags for every stylesheet used by the current render."""
        used: list[str] = context.vars.get(_USED_KEY, [])
        return Markup("").join(
            Markup('<link rel="stylesheet" href="{}" />').format(self._extension.stylesheet_url(path)) for path in used
        )
