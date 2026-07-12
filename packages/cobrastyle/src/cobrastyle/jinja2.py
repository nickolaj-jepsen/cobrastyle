from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, TypedDict, cast, overload

from jinja2 import Environment, TemplateSyntaxError, nodes, pass_context
from jinja2.ext import Extension
from jinja2.parser import Parser
from jinja2.runtime import Context
from markupsafe import Markup

from cobrastyle.cx import cx
from cobrastyle.errors import StylesheetNotFoundError, StylesheetPathError
from cobrastyle.fragments import fragment_links_html
from cobrastyle.manifest import Manifest, ModuleEntry
from cobrastyle.paths import normalize_path
from cobrastyle.resolvers import FileResolver, HasUrlPrefix
from cobrastyle.serve import hot_reload_script_html
from cobrastyle.source import StyleSource

if TYPE_CHECKING:
    from collections.abc import Callable

    from cobrastyle.manager import CobrastyleManager

_USED_KEY = "_cobrastyle_used"
_PAGE_GLOBAL = "__cobrastyle_page__"


class ConfigureOptions(TypedDict, total=False):
    """The mode-independent keyword options of :func:`configure`, for adapters that forward them."""

    minify: bool
    rewrite_class_names: bool
    module_pattern: str | None
    targets: list[str] | None
    source_map: bool


class ExtendedEnvironment(Environment):
    """Type-only view of an Environment carrying the attributes ``CobrastyleExtension`` adds
    via ``environment.extend()``. Never instantiated — see :func:`extended`."""

    globals: dict[str, Any]
    cobrastyle_resolver: FileResolver | None
    cobrastyle_manifest: Manifest | None
    cobrastyle_url_map: Callable[[ModuleEntry], str] | None
    cobrastyle_minify: bool
    cobrastyle_rewrite_class_names: bool
    cobrastyle_module_pattern: str | None
    cobrastyle_targets: list[str] | None
    cobrastyle_analyze_dependencies: bool
    cobrastyle_source_map: bool
    cobrastyle_hot_reload: str | None


def extended(environment: Environment) -> ExtendedEnvironment:
    """View ``environment`` through the attributes :class:`CobrastyleExtension` extends it with."""
    return cast(ExtendedEnvironment, environment)


class _CompileState(threading.local):
    """The template currently being compiled, per thread."""

    def __init__(self) -> None:
        self.page_id: str | None = None


@overload
def configure(
    environment: Environment,
    *,
    resolver: FileResolver,
    hot_reload: bool | str = ...,
    minify: bool = ...,
    rewrite_class_names: bool = ...,
    module_pattern: str | None = ...,
    targets: list[str] | None = ...,
    source_map: bool = ...,
) -> None: ...
@overload
def configure(
    environment: Environment,
    *,
    manifest: Manifest | str | Path,
    url_map: Callable[[ModuleEntry], str] | None = ...,
    minify: bool = ...,
    rewrite_class_names: bool = ...,
    module_pattern: str | None = ...,
    targets: list[str] | None = ...,
    source_map: bool = ...,
) -> None: ...
def configure(
    environment: Environment,
    *,
    resolver: FileResolver | None = None,
    manifest: Manifest | str | Path | None = None,
    url_map: Callable[[ModuleEntry], str] | None = None,
    hot_reload: bool | str = False,
    minify: bool = False,
    rewrite_class_names: bool = True,
    module_pattern: str | None = None,
    targets: list[str] | None = None,
    source_map: bool = True,
) -> None:
    """Configure cobrastyle on an environment using :class:`CobrastyleExtension`.

    Pass exactly one of ``resolver`` (dev mode: stylesheets compile on
    demand) or ``manifest`` (prod mode: class maps and URLs come from a
    prebuilt manifest, the compiler is never imported). Must be called
    before any template using ``{% cobrastyle %}`` is loaded.

    ``url_map`` (manifest mode only) derives each module's URL from its
    manifest entry instead of the one baked in at build time — for serving
    through a static-file pipeline that owns URL generation.

    ``hot_reload`` (dev mode only) makes ``links()`` also emit the
    hot-reload client script, which live-swaps stylesheets as their sources
    change. It requires the dev CSS server (its events endpoint) to be
    mounted at the resolver's URL prefix — the framework adapters do this
    and enable the flag; pass a prefix string instead of ``True`` when the
    resolver doesn't carry one.

    ``minify`` and ``source_map`` only shape dev-served CSS — readable
    output with a source map by default; the production build minifies
    regardless. ``module_pattern=None`` follows the same split: readable
    dev class names (``[name]_[local]_[hash]``), compact build names
    (``[hash]_[local]``). An explicit pattern applies to both.

    Dev mode rejects a ``bytecode_cache`` — a cache hit would silently skip
    the page tracking behind ``links()``; prod mode is cache-safe.
    """
    if (resolver is None) == (manifest is None):
        raise TypeError("Pass exactly one of resolver= (dev mode) or manifest= (prod mode)")
    if url_map is not None and manifest is None:
        raise TypeError("url_map= only applies to manifest mode; dev URLs come from the manager")
    if resolver is not None and environment.bytecode_cache is not None:
        raise RuntimeError(
            "cobrastyle dev mode is incompatible with a bytecode_cache: cache hits skip "
            "template compilation, silently breaking cobrastyle.links(). Remove the "
            "bytecode_cache or use a manifest in production."
        )
    hot_reload_prefix: str | None = None
    if hot_reload:
        if manifest is not None:
            raise TypeError("hot_reload= is dev-only; manifest mode serves static, immutable CSS")
        if isinstance(hot_reload, str):
            hot_reload_prefix = hot_reload
        elif isinstance(resolver, HasUrlPrefix):
            hot_reload_prefix = resolver.url_prefix
        else:
            raise TypeError(
                "hot_reload=True needs a resolver with a url_prefix; pass the serving "
                "prefix explicitly, e.g. hot_reload='/cobrastyle/'"
            )
    if isinstance(manifest, str | Path):
        manifest = Manifest.load(manifest)
    env = extended(environment)
    env.cobrastyle_resolver = resolver
    env.cobrastyle_manifest = manifest
    env.cobrastyle_url_map = url_map
    env.cobrastyle_hot_reload = hot_reload_prefix
    env.cobrastyle_minify = minify
    env.cobrastyle_rewrite_class_names = rewrite_class_names
    env.cobrastyle_module_pattern = module_pattern
    env.cobrastyle_targets = targets
    env.cobrastyle_source_map = source_map


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
            cobrastyle_url_map=None,
            cobrastyle_minify=False,
            cobrastyle_rewrite_class_names=True,
            cobrastyle_module_pattern=None,
            cobrastyle_targets=None,
            cobrastyle_analyze_dependencies=False,
            cobrastyle_source_map=True,
            cobrastyle_hot_reload=None,
        )
        extended(environment).globals["cobrastyle"] = CobrastyleRuntime(self)
        extended(environment).globals.setdefault("cx", cx)
        extended(environment).globals[_PAGE_GLOBAL] = self._enter_page
        self._manager: CobrastyleManager | None = None
        # Parse-time hook observing (assign target node, module path, class map)
        self._binding_recorder: Callable[[nodes.Node, str, dict[str, str]], None] | None = None
        # Module paths statically imported by each compiled template
        self._pages: dict[str, list[str]] = {}
        self._compiling = _CompileState()

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
                source_map=environment.cobrastyle_source_map,
            )
        return self._manager

    def preprocess(self, source: str, name: str | None, filename: str | None = None) -> str:
        # Either cache layer can skip preprocess/parse — never let a stale page_id leak into the next compile
        self._compiling.page_id = None
        if "cobrastyle" not in source:
            return source
        # Source-hashed, not a counter, so recompiling the same string (from_string
        # in a loop) reuses one registry entry instead of growing _pages forever
        page_id = name if name is not None else f"<anonymous-{hashlib.sha256(source.encode()).hexdigest()[:12]}>"
        self._pages[page_id] = []
        self._compiling.page_id = page_id
        # Prepended without a newline so template line numbers stay intact. The
        # top-level set runs at render start — before any parent template renders —
        # which lets {{ cobrastyle.links() }} in an inherited <head> see this
        # template's stylesheets.
        environment = self.environment
        return (
            f"{environment.block_start_string} set __cobrastyle__ = "
            f"{_PAGE_GLOBAL}({json.dumps(page_id)}) {environment.block_end_string}{source}"
        )

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

        try:
            path = normalize_path(path_expression.value)
        except StylesheetPathError as exc:
            raise TemplateSyntaxError(str(exc), lineno, parser.name, parser.filename) from exc
        classes, page_paths = self._resolve_module(path, lineno, parser)
        if self._binding_recorder is not None:
            self._binding_recorder(target, path, classes)

        page_id = self._compiling.page_id
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
            return StyleSource(manifest=manifest, url_map=extended(self.environment).cobrastyle_url_map)
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
        """Record the template's statically imported stylesheets in the render context.

        Prepended: parent templates enter after the extending child, but
        their (layout) modules must be linked first so a page module that
        re-declares shared rules wins the cascade.
        """
        used = context.vars.setdefault(_USED_KEY, [])
        used[:0] = [path for path in self.page_modules(page_id) if path not in used]
        return ""


class CobrastyleRuntime:
    """The ``cobrastyle`` template global."""

    def __init__(self, extension: CobrastyleExtension):
        self._extension = extension

    @pass_context
    def links(self, context: Context) -> Markup:
        """Render <link> tags for every stylesheet used by the current render.

        With ``hot_reload`` configured (dev mode), also emits the client
        script that live-swaps those links as their sources change.
        """
        used: list[str] = context.vars.get(_USED_KEY, [])
        markup = Markup("").join(
            Markup('<link rel="stylesheet" href="{}" />').format(self._extension.stylesheet_url(path)) for path in used
        )
        hot_reload = extended(self._extension.environment).cobrastyle_hot_reload
        if hot_reload is not None:
            markup += Markup(hot_reload_script_html(hot_reload))
        return markup

    @pass_context
    def fragment_links(self, context: Context, nonce: str | None = None) -> Markup:
        """Render fragment-response markup that loads the fragment's stylesheets.

        For templates rendered as partials (HTMX swaps), where no <head> —
        and so no ``links()`` — ever renders: emits an out-of-band script
        (``hx-swap-oob``) that adds the fragment's stylesheet links to the
        page's <head> before the swapped markup settles, skipping links the
        page already has. Empty when the fragment uses no modules. ``nonce``
        feeds the script's CSP nonce attribute.
        """
        used: list[str] = context.vars.get(_USED_KEY, [])
        urls = [self._extension.stylesheet_url(path) for path in used]
        return Markup(fragment_links_html(urls, nonce=nonce))
