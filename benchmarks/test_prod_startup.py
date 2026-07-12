"""Prod startup cost: parsing manifest.json into a Manifest — the per-process
price a serverless cold start or a many-worker deployment pays."""

from __future__ import annotations

from pathlib import Path

from pytest_benchmark.fixture import BenchmarkFixture

from cobrastyle.manifest import Manifest


def test_manifest_load(built: Path, benchmark: BenchmarkFixture) -> None:
    manifest = benchmark(Manifest.load, built / "manifest.json")

    assert manifest.modules
