import os
import re

import pytest

# The canonical dev-mode page shared by the framework e2e suites
PAGE_TEMPLATE = (
    "<html><head>{{ cobrastyle.links() }}</head>"
    '{% cobrastyle styles = "page.css" %}'
    '<body><h1 class="{{ styles.title }}">Hi</h1></body></html>'
)


def _extract(pattern: str, html: str) -> str:
    match = re.search(pattern, html)
    assert match is not None, f"{pattern!r} not found in {html!r}"
    return match.group(1)


@pytest.fixture
def extract():
    return _extract


@pytest.fixture
def page_project(tmp_path):
    """templates/index.html rendering styles/page.css — the shared e2e project layout."""
    (tmp_path / "templates").mkdir()
    (tmp_path / "styles").mkdir()
    (tmp_path / "styles" / "page.css").write_text(".title { color: red; }")
    os.utime(tmp_path / "styles" / "page.css", (1000, 1000))
    (tmp_path / "templates" / "index.html").write_text(PAGE_TEMPLATE)
    return tmp_path


def pytest_configure(config):
    try:
        import django
        from django.conf import settings
    except ImportError:
        return
    if not settings.configured:
        settings.configure(
            DEBUG=True,
            SECRET_KEY="test-only",
            ALLOWED_HOSTS=["testserver"],
            INSTALLED_APPS=["django.contrib.staticfiles", "cobrastyle.django"],
            DATABASES={},
            TEMPLATES=[],
            STATIC_URL="/static/",
            USE_TZ=True,
        )
        django.setup()
