from django import template
from django.template import TemplateSyntaxError
from django.template.base import FilterExpression
from django.utils.html import conditional_escape, escape
from django.utils.safestring import SafeString, mark_safe

from cobrastyle.cx import cx
from cobrastyle.django.runtime import get_runtime
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
    looked up in the manifest in prod.
    """
    bits = token.split_contents()
    if len(bits) != 4 or bits[2] != "as":
        raise TemplateSyntaxError('cobrastyle expects: {% cobrastyle "path/to.css" as styles %}')
    path = _constant_path(bits[1], "cobrastyle")

    runtime = get_runtime()
    classes, page_paths = runtime.resolve(path)
    runtime.record(getattr(parser, "origin", None), page_paths)
    return CobrastyleNode(bits[3], classes, path)


class CobrastyleNode(template.Node):
    def __init__(self, variable_name: str, classes: dict[str, str], path: str):
        self.variable_name = variable_name
        self.classes = classes
        self.path = path

    def render(self, context: template.Context) -> str:
        context[self.variable_name] = self.classes
        return ""


@register.tag("cobrastyle_links")
def cobrastyle_links(parser: template.base.Parser, token: template.base.Token) -> template.Node:
    """``{% cobrastyle_links %}`` — render <link> tags for the page's stylesheets.

    Collection is per top-level template, so a parent's <head> sees the
    extending child's stylesheets. Styles imported inside ``{% include %}``d
    templates are NOT seen; pass their paths explicitly:
    ``{% cobrastyle_links "shared/nav.css" %}``.
    """
    extra = tuple(_constant_path(bit, "cobrastyle_links") for bit in token.split_contents()[1:])
    return LinksNode(extra)


class LinksNode(template.Node):
    def __init__(self, extra: tuple[str, ...]):
        self.extra = extra

    def render(self, context: template.Context) -> SafeString:
        runtime = get_runtime()
        origin = getattr(getattr(context, "template", None), "origin", None)
        paths = list(self.extra)
        for path in runtime.page_modules(origin):
            if path not in paths:
                paths.append(path)
        return mark_safe("".join(f'<link rel="stylesheet" href="{escape(runtime.url_for(path))}" />' for path in paths))


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
