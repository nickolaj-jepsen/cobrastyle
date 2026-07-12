from __future__ import annotations

from pathlib import Path

import pytest
import synth

from cobrastyle.build import build
from cobrastyle.manager import CobrastyleManager

# 10 ≈ the example apps, 200 ≈ a large real app; the spread shows scaling curves.
SIZES = (10, 50, 200)


@pytest.fixture(scope="session", params=SIZES, ids=lambda size: f"{size:03d}mod")
def project(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> synth.Project:
    root = tmp_path_factory.mktemp(f"synth-{request.param}")
    return synth.generate_project(root, modules=request.param, pages=request.param * 2)


@pytest.fixture(scope="session")
def warm_manager(project: synth.Project) -> CobrastyleManager:
    manager = synth.make_manager(project)
    synth.warm(manager, project)
    return manager


@pytest.fixture(scope="session")
def built(project: synth.Project, tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp(f"dist-{project.modules}")
    build(synth.dev_environment(project), output_dir=output)
    return output


def pytest_configure(config: pytest.Config) -> None:
    try:
        import django
        from django.conf import settings
    except ImportError:
        return
    if not settings.configured:
        settings.configure(
            DEBUG=True,
            SECRET_KEY="bench-only",
            INSTALLED_APPS=["django.contrib.staticfiles", "cobrastyle.django"],
            DATABASES={},
            TEMPLATES=[],
            STATIC_URL="/static/",
            USE_TZ=True,
        )
        django.setup()
