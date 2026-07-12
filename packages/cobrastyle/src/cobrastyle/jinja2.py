from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Self, TypedDict, Unpack, cast, overload

from jinja2 import Environment, TemplateSyntaxError, nodes, pass_context
from jinja2.ext import Extension
from jinja2.parser import Parser
from jinja2.runtime import Context
from markupsafe import Markup

from cobrastyle.cx import cx
from cobrastyle.errors import StylesheetNotFoundError, StylesheetPathError
from cobrastyle.manifest import Manifest, ModuleEntry
from cobrastyle.paths import normalize_path
from cobrastyle.resolvers import FileResolver, FileSystemResolver, HasUrlPrefix
from cobrastyle.source import StyleSource

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from cobrastyle.manager import CobrastyleManager

_USED_KEY = "_cobrastyle_used"
_PAGE_GLOBAL = "__cobrastyle_page__"


class ConfigureOptions(TypedDict, total=False):
    """The compile options of :func:`configure`, forwarded verbatim to :class:`CobrastyleManager`.

    Mode-independent, so adapters forward them with ``**options``. Keys match
    the manager's keyword arguments exactly — that is the contract that lets
    the environment store one dict instead of a field per option.
    ``analyze_dependencies`` is the build's (it needs url()/@import
    placeholders); nothing else sets it.
    """

    minify: bool
    underscore_aliases: bool
    module_pattern: str | None
    targets: list[str] | None
    source_map: bool
    analyze_dependencies: bool
    global_patterns: Sequence[str] | None


class ExtendedEnvironment(Environment):
    """Type-only view of an Environment carrying the attributes ``CobrastyleExtension`` adds
    via ``environment.extend()``. Never instantiated — see :func:`extended`."""

    globals: dict[str, Any]
    cobrastyle_resolver: FileResolver | None
    cobrastyle_manifest: Manifest | None
    cobrastyle_url_map: Callable[[ModuleEntry], str] | None
    cobrastyle_hot_reload: str | None
    cobrastyle_options: ConfigureOptions


def extended(environment: Environment) -> ExtendedEnvironment:
    """View ``environment`` through the attributes :class:`CobrastyleExtension` extends it with."""
    return cast(ExtendedEnvironment, environment)


class TemplateRefs(NamedTuple):
    """The templates a source statically pulls in, split by how their modules cascade.

    ``inherited`` (``{% extends %}``) supplies the layout, whose stylesheets
    link before the template's own; ``included`` (``{% include %}``,
    ``{% import %}``, ``{% from %}``) supplies partials, whose stylesheets link
    after. Only constant names — a dynamic ``{% include page %}`` resolves to
    nothing here and its modules must be named in ``links()``.
    """

    inherited: tuple[str, ...] = ()
    included: tuple[str, ...] = ()


_NO_REFS = TemplateRefs()
_INCLUDING_TAGS = frozenset({"include", "import", "from"})
# The substrings any tag we scan for must contain — a source with none of them
# cannot reference cobrastyle or another template, so it never reaches the lexer.
# ({% from "x" import y %} always carries "import".)
_SCAN_HINTS = ("cobrastyle", "extends", "include", "import")


def _extend(modules: list[str], paths: Iterable[str]) -> None:
    """Append the paths ``modules`` does not already carry, preserving order."""
    for path in paths:
        if path not in modules:
            modules.append(path)


class _CompileState(threading.local):
    """The template currently being compiled, per thread.

    ``page_paths`` collects the template's module paths; the last of the
    ``pending_tags`` counted by preprocess() publishes it (with ``page_refs``)
    into the extension's page registry as one finished entry — deferred so a
    concurrent render never observes a half-built (or emptied) entry for a page
    it is rendering.
    """

    def __init__(self) -> None:
        self.page_id: str | None = None
        self.page_paths: list[str] | None = None
        self.page_refs: TemplateRefs = _NO_REFS
        self.pending_tags = 0


def _scan_source(environment: Environment, source: str, name: str | None) -> tuple[int | None, TemplateRefs]:
    """The number of ``cobrastyle`` tags the parser will fire for ``source``, and the templates it refers to.

    Lexer-exact, unlike a regex over the raw text: line-statement tags count,
    while tags inside comments or ``{% raw %}`` don't. The tag count is None
    when the source doesn't lex — the compile is about to fail anyway.
    """
    count = 0
    inherited: list[str] = []
    included: list[str] = []
    try:
        tokens = list(environment.lexer.tokenize(source, name))
    except TemplateSyntaxError:
        return None, _NO_REFS
    for index, token in enumerate(tokens[:-1]):
        if token.type != "block_begin":
            continue
        tag = tokens[index + 1]
        if tag.type != "name":
            continue
        if tag.value == "cobrastyle":
            count += 1
            continue
        if tag.value == "extends":
            refs = inherited
        elif tag.value in _INCLUDING_TAGS:
            refs = included
        else:
            continue
        referenced = tokens[index + 2] if index + 2 < len(tokens) else None
        if referenced is not None and referenced.type == "string" and referenced.value not in refs:
            refs.append(referenced.value)
    return count, TemplateRefs(tuple(inherited), tuple(included))


@overload
def configure(
    environment: Environment,
    *,
    resolver: FileResolver,
    hot_reload: bool | str = ...,
    minify: bool = ...,
    underscore_aliases: bool = ...,
    module_pattern: str | None = ...,
    targets: list[str] | None = ...,
    source_map: bool = ...,
    analyze_dependencies: bool = ...,
    global_patterns: Sequence[str] | None = ...,
) -> None: ...
@overload
def configure(
    environment: Environment,
    *,
    manifest: Manifest | str | Path,
    url_map: Callable[[ModuleEntry], str] | None = ...,
    minify: bool = ...,
    underscore_aliases: bool = ...,
    module_pattern: str | None = ...,
    targets: list[str] | None = ...,
    source_map: bool = ...,
    analyze_dependencies: bool = ...,
    global_patterns: Sequence[str] | None = ...,
) -> None: ...
def configure(
    environment: Environment,
    *,
    resolver: FileResolver | None = None,
    manifest: Manifest | str | Path | None = None,
    url_map: Callable[[ModuleEntry], str] | None = None,
    hot_reload: bool | str = False,
    minify: bool = False,
    underscore_aliases: bool = True,
    module_pattern: str | None = None,
    targets: list[str] | None = None,
    source_map: bool = True,
    analyze_dependencies: bool = False,
    global_patterns: Sequence[str] | None = None,
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

    ``global_patterns`` are the paths that compile unscoped, keeping the
    class names their author wrote and exporting none (default
    ``["*.global.css"]``; ``[]`` makes every stylesheet a module).

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
    # Rebound, never mutated in place: overlay() shallow-copies __dict__, so an
    # overlay (the build's, the check's) shares this dict object with its base.
    env.cobrastyle_options = ConfigureOptions(
        minify=minify,
        underscore_aliases=underscore_aliases,
        module_pattern=module_pattern,
        targets=targets,
        source_map=source_map,
        analyze_dependencies=analyze_dependencies,
        global_patterns=global_patterns,
    )
    # Drop the lazily-built manager and source (which carries the URL and markup
    # caches) so reconfiguration actually takes effect; templates already compiled
    # keep the class maps they baked in.
    extension = CobrastyleExtension.get(environment)
    if extension is not None:
        extension._manager = None
        extension._source = None
        # The new mode resolves pages differently (manifest vs. parse-time registry)
        extension._generation += 1


def configure_dev(
    environment: Environment,
    *,
    resolver: FileResolver | None = None,
    root: str | Path | None = None,
    url_prefix: str = "/cobrastyle/",
    hot_reload: bool = True,
    **options: Unpack[ConfigureOptions],
) -> tuple[CobrastyleExtension, str]:
    """Register the extension and configure dev mode; return it and the serving prefix.

    Pass a ``resolver`` or the ``root`` styles directory it should read. The
    returned prefix — the resolver's own when it carries one, else
    ``url_prefix`` — is where the dev CSS server must be mounted, and where the
    hot-reload client looks for its events endpoint.
    """
    environment.add_extension(CobrastyleExtension)
    if resolver is None:
        if root is None:
            raise TypeError("Provide either resolver=, root= (the styles directory), or manifest= (prod)")
        resolver = FileSystemResolver(root, url_prefix=url_prefix)
    prefix = resolver.url_prefix if isinstance(resolver, HasUrlPrefix) else url_prefix
    configure(environment, resolver=resolver, hot_reload=prefix if hot_reload else False, **options)
    extension = CobrastyleExtension.get(environment)
    assert extension is not None  # add_extension above guarantees it
    return extension, prefix


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
            cobrastyle_hot_reload=None,
            cobrastyle_options=ConfigureOptions(),
        )
        extended(environment).globals["cobrastyle"] = CobrastyleRuntime(self)
        extended(environment).globals.setdefault("cx", cx)
        extended(environment).globals[_PAGE_GLOBAL] = self._enter_page
        self._manager: CobrastyleManager | None = None
        self._manager_lock = threading.Lock()
        self._source: StyleSource | None = None
        # Parse-time hook observing (assign target node, module path, class map)
        self._binding_recorder: Callable[[nodes.Node, str, dict[str, str]], None] | None = None
        # Module paths statically imported by each compiled template, and the
        # templates it pulls in — page_modules() closes over both
        self._pages: dict[str, list[str]] = {}
        self._refs: dict[str, TemplateRefs] = {}
        # That closure, memoized: links() asks for it on every render of every
        # page. Stamped with the generation it was computed under, which every
        # compile bumps — a dev edit recompiles, so stale entries never survive.
        self._closures: dict[str, tuple[int, tuple[str, ...]]] = {}
        self._generation = 0
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
        # Double-checked: first accesses race on threaded dev servers, and a losing
        # instance would pin any SSE connection that captured it to a manager no
        # future compile populates — hot reload would silently die for that tab.
        if self._manager is None:
            with self._manager_lock:
                if self._manager is None:
                    environment = extended(self.environment)
                    if environment.cobrastyle_resolver is None:
                        raise RuntimeError(
                            "No stylesheet resolver configured; call cobrastyle.jinja2.configure() "
                            "with resolver= (dev) or manifest= (prod) before loading templates."
                        )
                    from cobrastyle.manager import CobrastyleManager

                    self._manager = CobrastyleManager(environment.cobrastyle_resolver, **environment.cobrastyle_options)
        return self._manager

    def preprocess(self, source: str, name: str | None, filename: str | None = None) -> str:
        # Either cache layer can skip preprocess/parse — never let stale state leak into the next compile
        self._compiling.page_id = None
        self._compiling.page_paths = None
        self._compiling.page_refs = _NO_REFS
        if not any(hint in source for hint in _SCAN_HINTS):
            return source
        # Source-hashed, not a counter, so recompiling the same string (from_string
        # in a loop) reuses one registry entry instead of growing _pages forever
        page_id = name if name is not None else f"<anonymous-{hashlib.sha256(source.encode()).hexdigest()[:12]}>"
        environment = self.environment
        tags, refs = _scan_source(environment, source, name)
        if tags == 0 and refs == _NO_REFS:
            # No tag ever fires and no template is pulled in (the substring hit was
            # e.g. {{ cobrastyle.links() }}): [] is the final value, and publishing
            # now keeps tag-removal edits fresh. Nothing to track, so skip the
            # injection a tag-free layout would otherwise pay for on every render.
            self._publish(page_id, [], refs)
            return source
        if tags == 0:
            # Nothing to defer — no parse() will fire — but the refs still matter:
            # a page with no tags of its own inherits and includes stylesheets.
            self._publish(page_id, [], refs)
        else:
            # Deferred: the last tag's parse() swaps the finished entry in atomically,
            # so a render racing this compile sees the previous complete entry, never
            # an emptied or half-built one. A failed compile leaves the old entry.
            self._compiling.page_id = page_id
            self._compiling.page_paths = []
            self._compiling.page_refs = refs
            self._compiling.pending_tags = tags if tags is not None else -1
        # Prepended without a newline so template line numbers stay intact. The
        # top-level set runs at render start — before any parent template renders —
        # which lets {{ cobrastyle.links() }} in an inherited <head> see this
        # template's stylesheets.
        return (
            f"{environment.block_start_string} set __cobrastyle__ = "
            f"{_PAGE_GLOBAL}({json.dumps(page_id)}) {environment.block_end_string}{source}"
        )

    def parse(self, parser: Parser) -> nodes.Node:
        lineno = next(parser.stream).lineno
        # `{% cobrastyle "reset.global.css" %}`: no class map worth binding (a global exports
        # nothing), just link it. A target would be a name the template never reads.
        target = None
        if not parser.stream.current.test("string"):
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
        if self._binding_recorder is not None and target is not None:
            self._binding_recorder(target, path, classes)

        page_id = self._compiling.page_id
        page = self._compiling.page_paths
        if page_id is not None and page is not None:
            for page_path in page_paths:
                if page_path not in page:
                    page.append(page_path)
            self._compiling.pending_tags -= 1
            if self._compiling.pending_tags == 0:
                self._publish(page_id, page, self._compiling.page_refs)

        if target is None:
            # The tag's whole effect is the page tracking above; emit a statement that does nothing
            return nodes.ExprStmt(nodes.Const(None)).set_lineno(lineno)
        return nodes.Assign(target, nodes.Const(classes)).set_lineno(lineno)

    def _resolve_module(self, path: str, lineno: int, parser: Parser) -> tuple[dict[str, str], tuple[str, ...]]:
        try:
            return self.source.resolve(path)
        except StylesheetNotFoundError as exc:
            raise TemplateSyntaxError(str(exc), lineno, parser.name, parser.filename) from exc

    @property
    def source(self) -> StyleSource:
        """The current-mode resolution source, cached until :func:`configure` drops it.

        A racing rebuild is benign: both racers wrap the same (lock-guarded)
        manager or manifest, and a StyleSource carries only caches of data
        derived from them.
        """
        source = self._source
        if source is None:
            environment = extended(self.environment)
            manifest = self.manifest
            if manifest is not None:
                source = StyleSource(manifest=manifest, url_map=environment.cobrastyle_url_map, markup=Markup)
            else:
                source = StyleSource(
                    manager=self.manager, hot_reload_prefix=environment.cobrastyle_hot_reload, markup=Markup
                )
            self._source = source
        return source

    def stylesheet_url(self, path: str) -> str:
        """Return the URL the module at ``path`` is served from."""
        return self.source.url_for(path)

    def page_modules(self, page_id: str) -> list[str]:
        """Return every module path the template ``page_id`` needs linked.

        The transitive closure over the templates it statically pulls in, so
        an ``{% include %}``d partial's stylesheet reaches the ``<head>`` that
        never mentions it. Ordered layouts first, then the template's own
        modules, then its partials' — a module re-declaring a rule its layout
        set wins the cascade.

        Each template's own contribution prefers this process's parse-time
        collection; it falls back to the manifest, which is what keeps
        ``links()`` correct in workers that loaded the template from a cache
        and never compiled it.
        """
        cached = self._closures.get(page_id)
        if cached is not None and cached[0] == self._generation:
            return list(cached[1])
        # Sampled before the walk, which compiles templates it has not seen and so
        # bumps the generation itself: that entry is stale on arrival and the next
        # render recomputes it once, over a registry that has stopped moving.
        generation = self._generation
        modules: list[str] = []
        self._collect_modules(page_id, modules, set())
        self._closures[page_id] = (generation, tuple(modules))
        return modules

    def _publish(self, page_id: str, modules: list[str], refs: TemplateRefs) -> None:
        """Register what a just-compiled template imports, invalidating the memoized closures."""
        self._pages[page_id] = modules
        self._refs[page_id] = refs
        self._generation += 1

    def _collect_modules(self, page_id: str, modules: list[str], seen: set[str]) -> None:
        if page_id in seen:
            return
        seen.add(page_id)
        own = self._pages.get(page_id)
        if own is None:
            # Not compiled in this process. A manifest entry is already the
            # closure, so it ends the walk; otherwise compile the template to
            # find out what it needs (dev, and any template the manifest has no
            # modules for).
            baked = self.source.manifest_pages(page_id)
            if baked is not None:
                _extend(modules, baked)
                return
            if not self._load_template(page_id):
                return
            own = self._pages.get(page_id, [])
        refs = self._refs.get(page_id, _NO_REFS)
        for parent in refs.inherited:
            self._collect_modules(parent, modules, seen)
        _extend(modules, own)
        for partial in refs.included:
            self._collect_modules(partial, modules, seen)

    def _load_template(self, name: str) -> bool:
        """Compile ``name`` so its modules and refs land in the registry; False when it cannot be loaded."""
        try:
            self.environment.get_template(name)
        except Exception:
            # A referenced template that does not exist or does not compile is
            # the compiler's problem to report when it renders, not links()'.
            return False
        return name in self._pages

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

    def stylesheet_url(self, path: str) -> str:
        """The URL the module at ``path`` is served from.

        For markup cobrastyle does not write itself — a ``<link rel="preload">``,
        say. A module named only here is not linked by :meth:`links`, which
        links what the render's templates import.
        """
        return self._extension.stylesheet_url(normalize_path(path))

    @pass_context
    def links(self, context: Context) -> Markup:
        """Render <link> tags for every stylesheet used by the current render.

        With ``hot_reload`` configured (dev mode), also emits the client
        script that live-swaps those links as their sources change.
        """
        used: list[str] = context.vars.get(_USED_KEY, [])
        # Already Markup: the source wraps (and caches) with the markup= it was built with
        return cast(Markup, self._extension.source.links_html(used))

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
        return cast(Markup, self._extension.source.fragment_links_html(used, nonce=nonce))
