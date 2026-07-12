"""Synthetic cobrastyle project generator plus the scenario helpers shared by
the benchmarks (conftest.py) and the profiling scripts.

Everything is deterministic: module i's content and every template's imports
are pure functions of the index, so runs are comparable across machines.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from cobrastyle.jinja2 import CobrastyleExtension, configure
from cobrastyle.manager import CobrastyleManager
from cobrastyle.resolvers import FileSystemResolver

RESET_CSS = """\
:root {
  --space: 8px;
  --radius: 4px;
  --ink: #1a1a1a;
}
* { box-sizing: border-box; }
"""

BASE_TEMPLATE = """\
{% cobrastyle layout = "mod_000.css" %}
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>{% block title %}bench{% endblock %}</title>
    {{ cobrastyle.links() }}
  </head>
  <body class="{{ layout.card }}">
    <nav class="{{ layout.title }}"><a href="/">Home</a></nav>
    {% block content %}{% endblock %}
  </body>
</html>
"""

PAGE_TEMPLATE = """\
{{% extends "base.html" %}}
{{% block title %}}Page {index} · bench{{% endblock %}}
{{% block content %}}
  {{% cobrastyle styles = "{first}" %}}
  {{% cobrastyle extra = "{second}" %}}
  <section class="{{{{ styles.card }}}}">
    <h1 class="{{{{ styles.title }}}}">Page {index}</h1>
    <p class="{{{{ extra['two-part'] }}}}">Deterministic filler prose for benchmarking.</p>
    <button class="{{{{ cx(styles.button, extra.button) }}}}">Go</button>
  </section>
{{% endblock %}}
"""


def module_name(index: int, modules: int) -> str:
    return f"mod_{index % modules:03d}.css"


def module_css(index: int) -> str:
    # Modules come in groups of four: a base plus three that compose from it,
    # giving realistic shallow composes chains (dep_mtimes of 2-3 entries each).
    group_base = index - index % 4
    lines = [
        '@import "./reset.css";',
        "",
        ".card {",
        *([f'  composes: card from "./mod_{group_base:03d}.css";'] if index != group_base else []),
        f"  padding: {8 + index % 7}px;",
        "  border-radius: var(--radius);",
        "  background: #fff;",
        "}",
        ".title {",
        f"  font-size: {14 + index % 5}px;",
        "  font-weight: 600;",
        "  color: var(--ink);",
        "}",
        ".button {",
        "  display: inline-flex;",
        "  padding: 4px 12px;",
        f"  border: 1px solid #c{index % 10}c;",
        "}",
        ".button:hover { border-color: #999; }",
        ".two-part { letter-spacing: .01em; }",
        "@media (min-width: 720px) {",
        f"  .card {{ padding: {16 + index % 7}px; }}",
        "}",
    ]
    if index % 8 == 1:
        lines.append('.icon { background-image: url("./icon.svg"); }')
    return "\n".join(lines) + "\n"


def page_html(index: int, modules: int) -> str:
    return PAGE_TEMPLATE.format(
        index=index,
        first=module_name(2 * index, modules),
        second=module_name(2 * index + 1, modules),
    )


@dataclass(frozen=True)
class Project:
    styles: Path
    templates: Path
    modules: int
    pages: int

    def module_path(self, index: int) -> str:
        return module_name(index, self.modules)

    def page_name(self, index: int) -> str:
        return f"page_{index % self.pages:04d}.html"


def generate_project(root: Path, *, modules: int, pages: int) -> Project:
    """Write a styles/ and templates/ tree under ``root``.

    Every module is referenced by at least one template, so a full template
    walk compiles all of them.
    """
    styles = root / "styles"
    templates = root / "templates"
    styles.mkdir(parents=True, exist_ok=True)
    templates.mkdir(parents=True, exist_ok=True)
    (styles / "reset.css").write_text(RESET_CSS)
    (styles / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"/>')
    for index in range(modules):
        (styles / module_name(index, modules)).write_text(module_css(index))
    (templates / "base.html").write_text(BASE_TEMPLATE)
    for index in range(pages):
        (templates / page_html_name(index)).write_text(page_html(index, modules))
    return Project(styles=styles, templates=templates, modules=modules, pages=pages)


def page_html_name(index: int) -> str:
    return f"page_{index:04d}.html"


def make_manager(project: Project) -> CobrastyleManager:
    return CobrastyleManager(FileSystemResolver(project.styles))


def warm(manager: CobrastyleManager, project: Project) -> None:
    for index in range(project.modules):
        manager.import_module(project.module_path(index))


def dev_environment(project: Project) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(project.templates),
        extensions=[CobrastyleExtension],
        autoescape=True,
    )
    configure(environment, resolver=FileSystemResolver(project.styles))
    return environment


def prod_environment(project: Project, manifest_path: Path) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(project.templates),
        extensions=[CobrastyleExtension],
        autoescape=True,
    )
    configure(environment, manifest=manifest_path)
    return environment
