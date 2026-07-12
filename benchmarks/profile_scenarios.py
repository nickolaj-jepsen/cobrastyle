"""cProfile the benchmark scenarios and write .pstats files plus a cumtime summary.

Usage: python benchmarks/profile_scenarios.py [scenario ...] [--size N] [--out DIR]
Run with no scenario names to profile all of them.
"""

from __future__ import annotations

import argparse
import cProfile
import pstats
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import synth

from cobrastyle.build import build
from cobrastyle.serve import poll_changes, serve

SUMMARY_LINES = 30


def dev_get(project: synth.Project) -> Callable[[], None]:
    manager = synth.make_manager(project)
    synth.warm(manager, project)
    paths = [project.module_path(index) for index in range(project.modules)]

    def run() -> None:
        for _ in range(50):
            for path in paths:
                serve(manager, path)

    return run


def dev_304(project: synth.Project) -> Callable[[], None]:
    manager = synth.make_manager(project)
    synth.warm(manager, project)
    requests = []
    for index in range(project.modules):
        path = project.module_path(index)
        served = serve(manager, path)
        assert served is not None
        requests.append((path, dict(served.headers)["ETag"]))

    def run() -> None:
        for _ in range(50):
            for path, etag in requests:
                serve(manager, path, if_none_match=etag)

    return run


def poll_tick(project: synth.Project) -> Callable[[], None]:
    manager = synth.make_manager(project)
    synth.warm(manager, project)
    seen: dict[str, tuple[object, ...]] = {}
    poll_changes(manager, seen)

    def run() -> None:
        # ~90s of one SSE connection at the 0.3s interval
        for _ in range(300):
            poll_changes(manager, seen)

    return run


def cold_compile(project: synth.Project) -> Callable[[], None]:
    def run() -> None:
        synth.warm(synth.make_manager(project), project)

    return run


def prod_render(project: synth.Project) -> Callable[[], None]:
    output = Path(tempfile.mkdtemp(prefix="cobrastyle-bench-dist-"))
    build(synth.dev_environment(project), output_dir=output)
    environment = synth.prod_environment(project, output / "manifest.json")
    templates = [environment.get_template(project.page_name(index)) for index in range(min(project.pages, 20))]

    def run() -> None:
        for _ in range(100):
            for template in templates:
                template.render()

    return run


def full_build(project: synth.Project) -> Callable[[], None]:
    output = Path(tempfile.mkdtemp(prefix="cobrastyle-bench-dist-"))

    def run() -> None:
        build(synth.dev_environment(project), output_dir=output)

    return run


SCENARIOS: dict[str, Callable[[synth.Project], Callable[[], None]]] = {
    "dev-get": dev_get,
    "dev-304": dev_304,
    "poll-tick": poll_tick,
    "cold-compile": cold_compile,
    "prod-render": prod_render,
    "full-build": full_build,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenarios", nargs="*", metavar="scenario", help=f"any of: {', '.join(SCENARIOS)} (default: all)"
    )
    parser.add_argument("--size", type=int, default=200, help="number of CSS modules (default 200)")
    parser.add_argument("--out", type=Path, default=Path("profiles"), help="output directory for .pstats + summaries")
    args = parser.parse_args()
    if unknown := [name for name in args.scenarios if name not in SCENARIOS]:
        parser.error(f"unknown scenarios: {', '.join(unknown)}")

    root = Path(tempfile.mkdtemp(prefix="cobrastyle-bench-synth-"))
    project = synth.generate_project(root, modules=args.size, pages=args.size * 2)
    args.out.mkdir(parents=True, exist_ok=True)

    for name in args.scenarios or SCENARIOS:
        run = SCENARIOS[name](project)
        profile = cProfile.Profile()
        profile.enable()
        run()
        profile.disable()

        stats_path = args.out / f"{name}-{args.size}mod.pstats"
        profile.dump_stats(stats_path)
        summary_path = stats_path.with_suffix(".txt")
        with summary_path.open("w") as out:
            stats = pstats.Stats(profile, stream=out).sort_stats("cumulative")
            stats.print_stats(SUMMARY_LINES)
            stats.sort_stats("tottime").print_stats(SUMMARY_LINES)
        print(f"{name}: wrote {stats_path} and {summary_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
