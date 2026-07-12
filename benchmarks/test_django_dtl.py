"""Django DTL render path, dev and prod — the one template integration the
Jinja2 benchmarks don't cover. Class maps bind at parse time; the render cost
is the links/url resolution in the two tags."""

from __future__ import annotations

from pathlib import Path

import pytest
import synth
from pytest_benchmark.fixture import BenchmarkFixture

django = pytest.importorskip("django")

from django.template import engines  # noqa: E402
from django.test import override_settings  # noqa: E402

from cobrastyle.django.runtime import set_runtime  # noqa: E402

DTL_PAGE = (
    "{% load cobrastyle %}"
    '{% cobrastyle "MOD_A" as styles %}'
    '{% cobrastyle "MOD_B" as extra %}'
    "<head>{% cobrastyle_links %}</head>"
    '<section class="{{ styles.card }}"><h1 class="{{ styles.title }}">Page</h1>'
    '<button class="{% cx styles.button extra.button %}">Go</button></section>'
)


@pytest.fixture
def dtl_templates(project: synth.Project, tmp_path: Path) -> Path:
    templates = tmp_path / "dtl"
    templates.mkdir()
    page = DTL_PAGE.replace("MOD_A", project.module_path(2)).replace("MOD_B", project.module_path(3))
    (templates / "page.html").write_text(page)
    return templates


def _template_settings(templates: Path) -> list[dict]:
    return [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(templates)],
            "APP_DIRS": False,
            "OPTIONS": {},
        }
    ]


@pytest.fixture
def fresh_runtime():
    set_runtime(None)
    yield
    set_runtime(None)


@pytest.mark.usefixtures("fresh_runtime")
def test_dtl_dev_render(project: synth.Project, dtl_templates: Path, benchmark: BenchmarkFixture) -> None:
    settings = {
        "DEBUG": True,
        "COBRASTYLE": {"ROOT": project.styles, "HOT_RELOAD": False},
        "TEMPLATES": _template_settings(dtl_templates),
    }
    with override_settings(**settings):
        template = engines["django"].get_template("page.html")
        assert '<link rel="stylesheet"' in template.render()

        benchmark(template.render)


@pytest.mark.usefixtures("fresh_runtime")
def test_dtl_prod_render(project: synth.Project, dtl_templates: Path, built: Path, benchmark: BenchmarkFixture) -> None:
    settings = {
        "DEBUG": False,
        "COBRASTYLE": {"MANIFEST": built / "manifest.json", "OUTPUT_DIR": built},
        "TEMPLATES": _template_settings(dtl_templates),
    }
    with override_settings(**settings):
        template = engines["django"].get_template("page.html")
        assert '<link rel="stylesheet"' in template.render()

        benchmark(template.render)
