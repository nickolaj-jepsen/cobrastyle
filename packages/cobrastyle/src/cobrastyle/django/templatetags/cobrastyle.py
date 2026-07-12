from django import template
from django.template import TemplateSyntaxError
from django.template.base import FilterExpression
from django.utils.html import conditional_escape
from django.utils.safestring import SafeString, mark_safe

from cobrastyle.cx import cx
from cobrastyle.django.runtime import CobrastyleNode, DTLRuntime, get_runtime
from cobrastyle.paths import normalize_path

register = template.Library()


def _constant_path(bit: str, tag: str) -> str:
    if len(bit) < 2 or bit[0] not in "\"'" or bit[-1] != bit[0]:
        raise TemplateSyntaxError(f'{tag} expects a constant string path, e.g. {{% {tag} "page.css" ... %}}')
    try:
        return normalize_path(bit[1:-1])
    except ValueError as exc:
        raise TemplateSyntaxError(str(exc)) from exc


@register.tag("cobrastyle")
def cobrastyle(parser: template.base.Parser, token: template.base.Token) -> template.Node:
    """``{% cobrastyle "styles/button.css" as styles %}`` — assign the module's class map.

    The stylesheet resolves at parse time: compiled through the manager in dev,
    looked up in the manifest in prod. Without ``as styles`` the tag claims the
    stylesheet for the page (``{% cobrastyle_links %}`` links it) but binds
    nothing — for styles a template pulls in without naming a class.

    In a template that ``{% extends %}`` another, an ``as`` clause must sit
    inside the block that uses it: Django renders nothing else of such a
    template, so a binding outside every block would never happen.
    """
    bits = token.split_contents()
    if len(bits) not in (2, 4) or (len(bits) == 4 and bits[2] != "as"):
        raise TemplateSyntaxError('cobrastyle expects: {% cobrastyle "path/to.css" [as styles] %}')
    path = _constant_path(bits[1], "cobrastyle")
    if len(bits) == 4:
        _reject_unreachable_binding(parser, path, bits[3])

    runtime = get_runtime()
    classes, page_paths = runtime.resolve(path)
    runtime.record(getattr(parser, "origin", None), page_paths)
    return CobrastyleNode(bits[3] if len(bits) == 4 else None, classes, path, page_paths)


def _reject_unreachable_binding(parser: template.base.Parser, path: str, variable_name: str) -> None:
    """Reject an ``as`` clause Django would never run.

    A template that ``{% extends %}`` another renders only its blocks, so a tag
    outside every block never binds its name and the class map silently comes
    out empty. The parser's open commands say exactly where the tag sits: still
    inside ``extends`` (which parses the whole child) and not inside a block.
    The stylesheet itself is claimed either way — the page walks the template
    graph, it does not wait for a tag to render — so the no-``as`` form stays
    legal there and is the fix when only the link is wanted.
    """
    commands = {command for command, _ in parser.command_stack}
    if "extends" not in commands or "block" in commands:
        return
    raise TemplateSyntaxError(
        f'{{% cobrastyle "{path}" as {variable_name} %}} sits outside every {{% block %}} of a template that '
        f"extends another. Django renders only the blocks of such a template, so {variable_name} would be empty "
        f'everywhere it is used. Move the tag into the block that uses it — or drop the "as" clause to link the '
        f'stylesheet without naming its classes here: {{% cobrastyle "{path}" %}}.'
    )


@register.tag("cobrastyle_url")
def cobrastyle_url(parser: template.base.Parser, token: template.base.Token) -> template.Node:
    """``{% cobrastyle_url "page.css" %}`` — the URL the module is served from.

    For markup cobrastyle does not write itself (a ``<link rel="preload">``,
    say). ``as var`` assigns instead of rendering. Note that a module named
    only here is not linked by ``{% cobrastyle_links %}``, which links what the
    page's templates import.
    """
    bits = token.split_contents()
    if len(bits) not in (2, 4) or (len(bits) == 4 and bits[2] != "as"):
        raise TemplateSyntaxError('cobrastyle_url expects: {% cobrastyle_url "path/to.css" [as var] %}')
    path = _constant_path(bits[1], "cobrastyle_url")
    url = get_runtime().url_for(path)
    return UrlNode(url, bits[3] if len(bits) == 4 else None)


class UrlNode(template.Node):
    def __init__(self, url: str, variable_name: str | None):
        self.url = url
        self.variable_name = variable_name

    def render(self, context: template.Context) -> str:
        if self.variable_name is None:
            return conditional_escape(self.url)
        context[self.variable_name] = self.url
        return ""


@register.tag("cobrastyle_links")
def cobrastyle_links(parser: template.base.Parser, token: template.base.Token) -> template.Node:
    """``{% cobrastyle_links %}`` — render <link> tags for the page's stylesheets.

    Covers every template the render statically pulls in — the whole
    ``{% extends %}`` chain and the partials they ``{% include %}`` — so a
    layout's ``<head>`` links the stylesheets of a page, and of a partial, that
    it never mentions. Layouts link first, so a page module re-declaring a
    shared rule wins the cascade. A dynamic ``{% include page %}`` names no
    template statically; pass such a partial's paths explicitly:
    ``{% cobrastyle_links "shared/nav.css" %}``.
    """
    extra = tuple(_constant_path(bit, "cobrastyle_links") for bit in token.split_contents()[1:])
    return LinksNode(extra)


def _page_paths(
    runtime: DTLRuntime,
    context: template.Context,
    origin: template.base.Origin | None,
    extra: tuple[str, ...],
) -> list[str]:
    """The module paths a links tag at ``origin`` covers: the render's whole template graph, layouts first.

    ``origin``'s own modules come last and usually add nothing — the graph
    already holds them. They matter when the graph cannot reach this template:
    a dynamic ``{% include page %}`` names no template statically.
    """
    paths = runtime.template_modules(getattr(context, "template", None))
    for path in (*runtime.page_modules(origin), *extra):
        if path not in paths:
            paths.append(path)
    return paths


class LinksNode(template.Node):
    def __init__(self, extra: tuple[str, ...]):
        self.extra = extra

    def render(self, context: template.Context) -> SafeString:
        runtime = get_runtime()
        # self.origin is stamped by the parser: the template this tag lives in
        paths = _page_paths(runtime, context, getattr(self, "origin", None), self.extra)
        return runtime.links_html(paths)


@register.tag("cobrastyle_fragment_links")
def cobrastyle_fragment_links(parser: template.base.Parser, token: template.base.Token) -> template.Node:
    """``{% cobrastyle_fragment_links %}`` — fragment-response variant of ``cobrastyle_links``.

    For templates rendered as partials (HTMX swaps), where no ``<head>`` ever
    renders: emits an out-of-band script (``hx-swap-oob``) that adds the
    fragment's stylesheet links to the page's ``<head>`` before the swapped
    markup settles, skipping links the page already has. Extra constant paths
    are accepted like ``cobrastyle_links``; ``nonce=var`` feeds the script's
    CSP nonce attribute: ``{% cobrastyle_fragment_links nonce=request.csp_nonce %}``.
    """
    nonce = None
    extra = []
    for bit in token.split_contents()[1:]:
        if bit.startswith("nonce="):
            nonce = parser.compile_filter(bit.removeprefix("nonce="))
        else:
            extra.append(_constant_path(bit, "cobrastyle_fragment_links"))
    return FragmentLinksNode(tuple(extra), nonce)


class FragmentLinksNode(template.Node):
    def __init__(self, extra: tuple[str, ...], nonce: FilterExpression | None):
        self.extra = extra
        self.nonce = nonce

    def render(self, context: template.Context) -> SafeString:
        runtime = get_runtime()
        paths = _page_paths(runtime, context, getattr(self, "origin", None), self.extra)
        nonce = self.nonce.resolve(context) if self.nonce is not None else None
        return runtime.fragment_links_html(paths, nonce=nonce or None)


# (value, condition, else-value)
_CxTerm = tuple[FilterExpression, "FilterExpression | None", "FilterExpression | None"]


@register.tag("cx")
def cx_tag(parser: template.base.Parser, token: template.base.Token) -> template.Node:
    """``{% cx a b if cond c if cond else d %}`` — join class names conditionally, clsx-style.

    Each space-separated term is a value, ``value if cond``, or ``value if cond
    else other``; falsy terms drop out. Values resolve with full
    :func:`cobrastyle.cx.cx` semantics, and the joined result is HTML-escaped
    and rendered inline.
    """
    bits = token.split_contents()[1:]
    if not bits:
        raise TemplateSyntaxError("cx expects at least one class term")
    terms: list[_CxTerm] = []
    i = 0
    while i < len(bits):
        value = parser.compile_filter(bits[i])
        condition = alternative = None
        i += 1
        if i < len(bits) and bits[i] == "if":
            i += 1
            if i >= len(bits):
                raise TemplateSyntaxError("cx: 'if' must be followed by a condition")
            condition = parser.compile_filter(bits[i])
            i += 1
            if i < len(bits) and bits[i] == "else":
                i += 1
                if i >= len(bits):
                    raise TemplateSyntaxError("cx: 'else' must be followed by a value")
                alternative = parser.compile_filter(bits[i])
                i += 1
        terms.append((value, condition, alternative))
    return CxNode(terms)


class CxNode(template.Node):
    def __init__(self, terms: list[_CxTerm]):
        self.terms = terms

    def render(self, context: template.Context) -> SafeString:
        parts: list[str] = []
        for value, condition, alternative in self.terms:
            # ignore_failures: a missing condition variable is False, matching {% if %},
            # not the engine's string_if_invalid (truthy when set for debugging)
            if condition is not None and not condition.resolve(context, ignore_failures=True):
                resolved = alternative.resolve(context) if alternative is not None else None
            else:
                resolved = value.resolve(context)
            rendered = cx(resolved)
            if rendered:
                parts.append(conditional_escape(rendered))
        return mark_safe(" ".join(parts))
