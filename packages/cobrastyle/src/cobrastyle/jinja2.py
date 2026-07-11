import json
import threading
from itertools import count

from jinja2 import Environment, TemplateSyntaxError, nodes, pass_context
from jinja2.ext import Extension
from jinja2.parser import Parser
from jinja2.runtime import Context
from markupsafe import Markup

from cobrastyle.manager import CobrastyleManager
from cobrastyle.resolvers import FileResolver

_USED_KEY = "_cobrastyle_used"
_PAGE_GLOBAL = "__cobrastyle_page__"


def configure(
    environment: Environment,
    *,
    resolver: FileResolver,
    minify: bool = True,
    rewrite_class_names: bool = True,
    module_pattern: str | None = None,
    targets: list[str] | None = None,
) -> None:
    """Configure cobrastyle on an environment using :class:`CobrastyleExtension`.

    Must be called before any template using ``{% cobrastyle %}`` is loaded.
    """
    environment.cobrastyle_resolver = resolver  # type: ignore[attr-defined]
    environment.cobrastyle_minify = minify  # type: ignore[attr-defined]
    environment.cobrastyle_rewrite_class_names = rewrite_class_names  # type: ignore[attr-defined]
    environment.cobrastyle_module_pattern = module_pattern  # type: ignore[attr-defined]
    environment.cobrastyle_targets = targets  # type: ignore[attr-defined]


class CobrastyleExtension(Extension):
    """Jinja2 extension that compiles CSS modules at template compile time.

    Usage::

        {% cobrastyle styles = "button.css" %}
        <button class="{{ styles.button }}">Click me</button>

    and in the layout's ``<head>``: ``{{ cobrastyle.links() }}``.

    Configure through environment attributes after constructing the environment:
    ``cobrastyle_resolver`` (required), ``cobrastyle_minify``,
    ``cobrastyle_rewrite_class_names``, ``cobrastyle_module_pattern`` and
    ``cobrastyle_targets``.
    """

    tags: set[str] = {"cobrastyle"}  # noqa: RUF012 — matches jinja2's Extension.tags declaration

    def __init__(self, environment: Environment):
        super().__init__(environment)
        environment.extend(
            cobrastyle_resolver=None,
            cobrastyle_minify=True,
            cobrastyle_rewrite_class_names=True,
            cobrastyle_module_pattern=None,
            cobrastyle_targets=None,
        )
        environment.globals["cobrastyle"] = CobrastyleRuntime(self)  # type: ignore[unsupported-operation]
        environment.globals[_PAGE_GLOBAL] = self._enter_page  # type: ignore[unsupported-operation]
        self._manager: CobrastyleManager | None = None
        # Stylesheet URLs statically imported by each compiled template
        self._pages: dict[str, list[str]] = {}
        self._anonymous_ids = count()
        self._compiling = threading.local()

    @property
    def manager(self) -> CobrastyleManager:
        if self._manager is None:
            environment = self.environment
            if environment.cobrastyle_resolver is None:  # type: ignore[attr-defined]
                raise RuntimeError(
                    "No stylesheet resolver configured; set environment.cobrastyle_resolver before loading templates."
                )
            self._manager = CobrastyleManager(
                environment.cobrastyle_resolver,  # type: ignore[attr-defined]
                minify=environment.cobrastyle_minify,  # type: ignore[attr-defined]
                module_pattern=environment.cobrastyle_module_pattern,  # type: ignore[attr-defined]
                rewrite_class_names=environment.cobrastyle_rewrite_class_names,  # type: ignore[attr-defined]
                targets=environment.cobrastyle_targets,  # type: ignore[attr-defined]
            )
        return self._manager

    def preprocess(self, source: str, name: str | None, filename: str | None = None) -> str:
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

        stylesheet = self.manager.import_module(path_expression.value)

        page_id: str | None = getattr(self._compiling, "page_id", None)
        if page_id is not None:
            page = self._pages[page_id]
            if stylesheet.url not in page:
                page.append(stylesheet.url)

        return nodes.Assign(target, nodes.Const(stylesheet.classes)).set_lineno(lineno)

    @pass_context
    def _enter_page(self, context: Context, page_id: str) -> str:
        """Record the template's statically imported stylesheets in the render context."""
        used = context.vars.setdefault(_USED_KEY, [])
        for url in self._pages.get(page_id, ()):
            if url not in used:
                used.append(url)
        return ""


class CobrastyleRuntime:
    """The ``cobrastyle`` template global."""

    def __init__(self, extension: CobrastyleExtension):
        self._extension = extension

    @pass_context
    def links(self, context: Context) -> Markup:
        """Render <link> tags for every stylesheet used by the current render."""
        used: list[str] = context.vars.get(_USED_KEY, [])
        return Markup("").join(Markup('<link rel="stylesheet" href="{}" />').format(url) for url in used)
