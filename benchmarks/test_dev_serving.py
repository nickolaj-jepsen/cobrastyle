"""Dev-mode request serving: the per-request cost of a warm cache, and the cold compile."""

from __future__ import annotations

import synth

from cobrastyle.serve import serve


def test_cache_hit_get(benchmark, warm_manager, project):
    path = project.module_path(1)
    warmed = serve(warm_manager, path)
    assert warmed is not None
    assert warmed.status == 200

    result = benchmark(serve, warm_manager, path)
    assert result.status == 200


def test_revalidation_304(benchmark, warm_manager, project):
    path = project.module_path(1)
    warmed = serve(warm_manager, path)
    assert warmed is not None
    etag = dict(warmed.headers)["ETag"]

    result = benchmark(serve, warm_manager, path, if_none_match=etag)
    assert result.status == 304


def test_raw_asset_get(benchmark, warm_manager):
    result = benchmark(serve, warm_manager, "icon.svg")
    assert result.status == 200


def test_cold_compile_all(benchmark, project):
    """First-page-load shape: every module compiles serially through one manager."""

    def compile_all(manager):
        synth.warm(manager, project)

    benchmark.pedantic(compile_all, setup=lambda: ((synth.make_manager(project),), {}), rounds=5)
