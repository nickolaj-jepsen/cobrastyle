from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.template.base import FilterExpression, Node, NodeList, Origin, Variable
from django.template.defaulttags import ForNode, WithNode

from cobrastyle.build import DEFAULT_GLOBS
from cobrastyle.check import Reference, TemplateScan, UsageCollector
from cobrastyle.django.build import _parse_templates, _throwaway_runtime
from cobrastyle.django.runtime import DTLRuntime
from cobrastyle.django.templatetags.cobrastyle import CobrastyleNode
from cobrastyle.manager import CobrastyleManager

if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.template.backends.django import DjangoTemplates

    from cobrastyle.jinja2 import ConfigureOptions
    from cobrastyle.resolvers import FileResolver

# Tags that assign their result to a context name (url/cycle/regroup/i18n "as var"); best-effort taint
_REBINDING_ATTRS = ("asvar", "varname", "var_name", "variable", "variable_name", "target_var")


def check_dtl(
    backend: DjangoTemplates,
    resolver: FileResolver,
    manager_options: ConfigureOptions,
    collector: UsageCollector,
    *,
    globs: tuple[str, ...] = DEFAULT_GLOBS,
    strict: bool = False,
) -> None:
    """Parse every DTL template in the backend and feed its scan into ``collector``.

    DTL variable resolution is dynamic, so coverage is best-effort: rebinding
    detection knows ``for``/``with`` and the common ``as var`` tags, and any
    unattributable use of a class map marks its module fully used rather than
    risking a false positive.
    """
    manager = CobrastyleManager(resolver, **manager_options)
    with _throwaway_runtime(backend, DTLRuntime(manager=manager)):
        for name, template in _parse_templates(backend, globs, strict):
            collector.add(_scan_nodelist(name, template.template.nodelist, collector))


def _scan_nodelist(template_name: str, nodelist: NodeList, collector: UsageCollector) -> TemplateScan:
    scan = TemplateScan(template_name)
    for node in nodelist.get_nodes_by_type(Node):
        lineno = getattr(getattr(node, "token", None), "lineno", None)
        if isinstance(node, CobrastyleNode):
            collector.register(node.path, node.classes)
            scan.bindings.append((node.variable_name, node.path))
            continue
        if isinstance(node, ForNode):
            scan.stored.update(node.loopvars)
        if isinstance(node, WithNode):
            scan.stored.update(node.extra_context)
        for attribute in _REBINDING_ATTRS:
            value = getattr(node, attribute, None)
            if isinstance(value, str):
                scan.stored.add(value)
        for variable in _variables(vars(node), set()):
            lookups = variable.lookups
            if not lookups:
                continue
            if len(lookups) == 1:
                scan.escaped.add(lookups[0])
            else:
                display = f"{lookups[0]}.{lookups[1]}"
                # DTL resolution tries the dict key first, then falls back to
                # attributes/methods — the collector resolves which one this is
                scan.references.append(
                    Reference(lookups[0], lookups[1], lineno, display, subscript_first=True, attr_fallback=True)
                )
    return scan


def _variables(value: Any, seen: set[int], depth: int = 0) -> Iterator[Variable]:
    """Best-effort sweep for the Variables a node holds, wherever its tag class stored them."""
    if depth > 8:
        return
    if isinstance(value, FilterExpression):
        yield from _variables(value.var, seen, depth + 1)
        for _, arguments in value.filters:
            # Argument tuples are (lookup, arg): lookup=True marks a Variable, False a resolved constant
            for lookup, argument in arguments:
                if lookup:
                    yield from _variables(argument, seen, depth + 1)
    elif isinstance(value, Variable):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _variables(item, seen, depth + 1)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _variables(item, seen, depth + 1)
    elif isinstance(value, (Node, NodeList, Origin, str, bytes, bool, int, float)) or value is None:
        # Nodes and their nodelists are walked by the caller; the rest can't hold Variables
        return
    elif type(value).__module__.startswith("django.template") and id(value) not in seen:
        # Condition wrappers (smartif operators, TemplateLiteral) bury expressions in plain attributes
        seen.add(id(value))
        yield from _variables(vars(value), seen, depth + 1)
