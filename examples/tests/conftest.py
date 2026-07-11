import shutil
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent


@pytest.fixture
def copy_example(tmp_path):
    """Copy an example into tmp_path so builds never dirty the repo tree."""

    def copy(name: str) -> Path:
        project = tmp_path / name
        shutil.copytree(EXAMPLES / name, project)
        return project

    return copy


def pytest_configure(config):
    try:
        import django
        from django.conf import settings
    except ImportError:
        return
    # Running examples/tests alone; the package conftest configures this otherwise
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
