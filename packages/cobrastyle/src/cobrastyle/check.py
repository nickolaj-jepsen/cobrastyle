from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from jinja2 import nodes

from cobrastyle.build import DEFAULT_GLOBS, BuildError, _enumerate, handle_compile_failure
from cobrastyle.jinja2 import CobrastyleExtension

if TYPE_CHECKING:
    from jinja2 import Environment


class Reference(NamedTuple):
    """One statically-known class access (``styles.button`` or ``styles["button"]``)."""

    name: str
    attr: str
    lineno: int | None
    display: str
    # Whether the engine tries the dict key before attributes (DTL, jinja subscripts);
    # jinja getattr is attribute-first, so the dict API shadows same-named classes
    subscript_first: bool


@dataclass
class TemplateScan:
    """What one template's static analysis saw; the collector resolves scans against each other."""

    template: str
    # binding name → module path, one entry per {% cobrastyle %} tag
    bindings: list[tuple[str, str]] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    # Names (re)bound by anything other than a cobrastyle tag
    stored: set[str] = field(default_factory=set)
    # Names used whole (passed around, aliased) — their accesses can't be attributed
    escaped: set[str] = field(default_factory=set)
    # Names subscripted with a non-constant key
    dynamic: set[str] = field(default_factory=set)


@dataclass
class UnknownClass:
    """A reference that provably does not yield a class: unknown, or shadowed by the dict API."""

    template: str
    lineno: int | None
    reference: str
    attr: str
    modules: tuple[str, ...]
    suggestion: str | None = None
    shadowed: bool = False

    def __str__(self) -> str:
        location = f"{self.template}:{self.lineno}" if self.lineno else self.template
        modules = ", ".join(repr(m) for m in self.modules)
        hint = f" (did you mean {self.suggestion!r}?)" if self.suggestion else ""
        if self.shadowed:
            return f"{location}: {self.reference} — class {self.attr!r} in {modules} is shadowed by the dict API{hint}"
        return f"{location}: {self.reference} — no class {self.attr!r} in {modules}{hint}"


@dataclass
class CheckReport:
    unknown: list[UnknownClass]
    unused: dict[str, list[str]]
    templates: int
    modules: int
    references: int

    @property
    def ok(self) -> bool:
        return not self.unknown

    def error_lines(self) -> list[str]:
        return [str(problem) for problem in self.unknown]

    def warning_lines(self) -> list[str]:
        return [f"{path}: unused class(es), best-effort: {', '.join(names)}" for path, names in self.unused.items()]

    def summary(self) -> str:
        return f"Checked {self.templates} template(s), {self.references} reference(s) across {self.modules} module(s)"

    def failures(self, *, strict_unused: bool = False) -> list[str]:
        problems = []
        if self.unknown:
            problems.append(f"{len(self.unknown)} unknown class reference(s)")
        if strict_unused and self.unused:
            problems.append(f"{len(self.unused)} module(s) with unused classes")
        return problems


class UsageCollector:
    """Accumulates template scans and module class maps (across engines), then resolves them into a report.

    Resolution never produces a false positive: a reference is only flagged
    when its binding is provably a cobrastyle class map in the same template,
    and a module's exports only count as unused when every access to the map
    was statically attributable.
    """

    def __init__(self) -> None:
        self.class_maps: dict[str, dict[str, str]] = {}
        self.scans: list[TemplateScan] = []

    def register(self, path: str, classes: dict[str, str]) -> None:
        self.class_maps.setdefault(path, dict(classes))

    def add(self, scan: TemplateScan) -> None:
        self.scans.append(scan)

    def report(self) -> CheckReport:
        # extends/include let a template reference a binding made elsewhere; those
        # count toward usage but are never flagged — only same-template bindings are provable
        global_bindings: dict[str, set[str]] = {}
        for scan in self.scans:
            for name, path in scan.bindings:
                global_bindings.setdefault(name, set()).add(path)

        used: dict[str, set[str]] = {path: set() for path in self.class_maps}
        fully_used: set[str] = set()
        unknown: list[UnknownClass] = []
        references = 0

        for scan in self.scans:
            local: dict[str, set[str]] = {}
            for name, path in scan.bindings:
                local.setdefault(name, set()).add(path)
            tainted = scan.stored & local.keys()
            for name in tainted:
                fully_used.update(local[name])
            for name in scan.escaped | scan.dynamic:
                paths = local[name] if name in local and name not in tainted else global_bindings.get(name, set())
                fully_used.update(paths)
            for name, attr, lineno, display, subscript_first in scan.references:
                verifiable = name in local and name not in tainted
                paths = local[name] if verifiable else global_bindings.get(name, set())
                if not paths:
                    continue
                references += 1
                exporting = [path for path in paths if attr in self.class_maps[path]]
                if not subscript_first and hasattr({}, attr):
                    # jinja getattr resolves attributes first, so the dict API wins
                    # even over an exported class — dot access never yields it
                    fully_used.update(paths)
                    if exporting and verifiable:
                        unknown.append(
                            UnknownClass(
                                scan.template,
                                lineno,
                                display,
                                attr,
                                tuple(sorted(paths)),
                                suggestion=f'{name}["{attr}"]',
                                shadowed=True,
                            )
                        )
                elif exporting:
                    for path in exporting:
                        used[path].add(attr)
                elif verifiable:
                    exports = sorted({export for path in paths for export in self.class_maps[path]})
                    suggestion = next(iter(difflib.get_close_matches(attr, exports, n=1)), None)
                    unknown.append(UnknownClass(scan.template, lineno, display, attr, tuple(sorted(paths)), suggestion))

        unknown.sort(key=lambda problem: (problem.template, problem.lineno or 0))
        unused = self._unused(used, fully_used)
        return CheckReport(unknown, unused, len(self.scans), len(self.class_maps), references)

    def _unused(self, used: dict[str, set[str]], fully_used: set[str]) -> dict[str, list[str]]:
        used_tokens: set[str] = set()
        for path, names in used.items():
            for name in names:
                used_tokens.update(self.class_maps[path][name].split())
        for path in fully_used:
            for value in self.class_maps.get(path, {}).values():
                used_tokens.update(value.split())

        unused: dict[str, list[str]] = {}
        for path in sorted(self.class_maps):
            if path in fully_used:
                continue
            classes = self.class_maps[path]
            names_used = used[path]
            remaining = [
                name
                for name, value in classes.items()
                if name not in names_used
                and not _is_rewrite_alias(name, classes)
                and not ("-" in name and name.replace("-", "_") in names_used)
                # A used class elsewhere composes this one: its output tokens all reappear there
                and not all(token in used_tokens for token in value.split())
            ]
            if remaining:
                unused[path] = sorted(remaining)
        return unused


def _is_rewrite_alias(name: str, classes: dict[str, str]) -> bool:
    """True for the underscore alias the manager adds beside a dashed class name."""
    dashed = name.replace("_", "-")
    return dashed != name and classes.get(dashed) == classes[name]


def check_jinja2(
    environment: Environment,
    collector: UsageCollector,
    *,
    globs: tuple[str, ...] = DEFAULT_GLOBS,
    strict: bool = False,
    extra_templates: tuple[str, ...] = (),
) -> None:
    """Parse every template in the environment and feed its scan into ``collector``.

    Class maps resolve the way the environment is configured: through the
    resolver in dev mode (no built manifest needed), from the manifest in prod
    mode. Templates that fail to parse follow the build's rules: skipped with
    a warning unless they mention cobrastyle or ``strict`` is set — then it's
    a :class:`BuildError`.
    """
    check_env = environment.overlay()
    extension = CobrastyleExtension.get(check_env)
    if extension is None:
        raise BuildError("CobrastyleExtension is not registered on the environment")
    if check_env.loader is None:
        raise BuildError("The environment has no loader; there are no templates to check")

    recorded: list[tuple[nodes.Node, str]] = []

    def record(target: nodes.Node, path: str, classes: dict[str, str]) -> None:
        collector.register(path, classes)
        recorded.append((target, path))

    extension._binding_recorder = record
    for name in _enumerate(check_env, globs, extra_templates):
        try:
            source, filename, _ = check_env.loader.get_source(check_env, name)
        except Exception as exc:
            raise BuildError(f"Cannot load template {name!r}: {exc}") from exc
        recorded.clear()
        try:
            ast = check_env.parse(source, name=name, filename=filename)
        except Exception as exc:
            handle_compile_failure(name, source, exc, strict)
            continue
        collector.add(scan_jinja2_ast(name, ast, recorded))


def scan_jinja2_ast(template: str, ast: nodes.Template, bindings: list[tuple[nodes.Node, str]]) -> TemplateScan:
    """Statically scan a parsed template for class-map bindings, accesses, rebinds and escapes."""
    scan = TemplateScan(template)
    binding_targets: set[int] = set()
    for target, path in bindings:
        if isinstance(target, nodes.Name):
            scan.bindings.append((target.name, path))
            binding_targets.add(id(target))

    consumed: set[int] = set()
    called = {id(call.node) for call in ast.find_all(nodes.Call)}
    for node in ast.find_all((nodes.Getattr, nodes.Getitem)):
        base = node.node
        if not isinstance(base, nodes.Name) or base.ctx != "load":
            continue
        consumed.add(id(base))
        if isinstance(node, nodes.Getattr):
            if id(node) in called:
                # A method call (styles.items(), styles.get(...)) — dict-API use of the whole map
                scan.dynamic.add(base.name)
            else:
                display = f"{base.name}.{node.attr}"
                scan.references.append(Reference(base.name, node.attr, node.lineno, display, subscript_first=False))
        elif isinstance(node.arg, nodes.Const) and isinstance(node.arg.value, str):
            display = f'{base.name}["{node.arg.value}"]'
            scan.references.append(Reference(base.name, node.arg.value, node.lineno, display, subscript_first=True))
        else:
            scan.dynamic.add(base.name)

    for name_node in ast.find_all(nodes.Name):
        if id(name_node) in binding_targets or id(name_node) in consumed:
            continue
        if name_node.ctx == "load":
            scan.escaped.add(name_node.name)
        else:
            scan.stored.add(name_node.name)
    for nsref in ast.find_all(nodes.NSRef):
        scan.stored.add(nsref.name)
    for from_import in ast.find_all(nodes.FromImport):
        for item in from_import.names:
            scan.stored.add(item[1] if isinstance(item, tuple) else item)
    for import_node in ast.find_all(nodes.Import):
        scan.stored.add(import_node.target)
    return scan
