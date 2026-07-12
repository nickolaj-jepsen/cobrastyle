"""Prod render path: a full template render in manifest mode, links() included."""

from __future__ import annotations

import synth


def test_render_page(benchmark, project, built):
    environment = synth.prod_environment(project, built / "manifest.json")
    template = environment.get_template(project.page_name(0))
    html = template.render()
    assert '<link rel="stylesheet"' in html

    benchmark(template.render)


FRAGMENT_TEMPLATE = (
    '{% cobrastyle styles = "MOD" %}'
    '<section class="{{ styles.card }}"><h1 class="{{ styles.title }}">Partial</h1></section>'
    "{{ cobrastyle.fragment_links() }}"
)


def test_render_fragment(benchmark, project, built):
    environment = synth.prod_environment(project, built / "manifest.json")
    template = environment.from_string(FRAGMENT_TEMPLATE.replace("MOD", project.module_path(2)))
    html = template.render()
    assert "hx-swap-oob" in html

    benchmark(template.render)
