"""Full production build: template walk, every module compiled, hashed output emitted."""

from __future__ import annotations

import synth

from cobrastyle.build import build


def test_full_build(benchmark, project, tmp_path):
    def run():
        return build(synth.dev_environment(project), output_dir=tmp_path)

    manifest = benchmark.pedantic(run, rounds=3)
    assert len(manifest.modules) == project.modules
