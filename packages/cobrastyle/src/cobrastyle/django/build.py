from __future__ import annotations

import fnmatch
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from cobrastyle.build import BUILD_MODULE_PATTERN, DEFAULT_GLOBS, CollectedTemplates, handle_compile_failure
from cobrastyle.django.runtime import DTLRuntime, set_runtime
from cobrastyle.jinja2 import ConfigureOptions
from cobrastyle.manager import CobrastyleManager
from cobrastyle.resolvers import FileResolver

if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.template.backends.django import DjangoTemplates
    from django.template.backends.django import Template as DjangoTemplate
    from django.template.engine import Engine


@contextmanager
def _throwaway_runtime(backend: DjangoTemplates, runtime: DTLRuntime) -> Iterator[None]:
    """Install ``runtime`` for a template walk over fresh loader caches, resetting both on exit.

    Django caches parsed templates even in DEBUG (4.1+) and ``{% cobrastyle %}``
    fires at parse time, so templates rendered earlier in this process would
    silently drop out of the walk without the first reset — and templates
    cached during the walk were parsed under the throwaway runtime, whose page
    records die with it, so later renders must re-parse.
    """
    set_runtime(runtime)
    try:
        reset_loaders(backend)
        yield
    finally:
        set_runtime(None)
        reset_loaders(backend)


def _parse_templates(
    backend: DjangoTemplates, globs: tuple[str, ...], strict: bool
) -> Iterator[tuple[str, DjangoTemplate]]:
    """Yield (name, parsed template) for every matching template, applying the build's failure rules."""
    for name, file in _template_files(backend.engine, globs):
        try:
            yield name, backend.get_template(name)
        except Exception as exc:
            handle_compile_failure(name, file.read_text(encoding="utf-8", errors="replace"), exc, strict)


def collect_dtl(
    backend: DjangoTemplates,
    resolver: FileResolver,
    manager_options: ConfigureOptions,
    *,
    globs: tuple[str, ...] = DEFAULT_GLOBS,
    strict: bool = False,
    minify: bool = True,
) -> CollectedTemplates:
    """Walk a DjangoTemplates backend's templates; return the pages, compiled stylesheets, and their resolver.

    Compiling a DTL template fires ``{% cobrastyle %}`` at parse time; a
    temporary dev runtime with dependency analysis collects the modules.
    """
    # Build output is production CSS regardless of the dev-serving options
    options: ConfigureOptions = {**manager_options, "minify": minify, "source_map": False}
    if options.get("module_pattern") is None:
        options["module_pattern"] = BUILD_MODULE_PATTERN
    manager = CobrastyleManager(resolver, analyze_dependencies=True, **options)
    runtime = DTLRuntime(manager=manager)
    with _throwaway_runtime(backend, runtime):
        for _ in _parse_templates(backend, globs, strict):
            pass

    pages = {
        runtime.page_names[origin_name]: paths
        for origin_name, paths in runtime.pages.items()
        if paths and origin_name in runtime.page_names
    }
    return CollectedTemplates(pages, manager.stylesheets, resolver)


def reset_loaders(backend: DjangoTemplates) -> None:
    """Drop the backend's parsed-template caches."""
    for loader in backend.engine.template_loaders:
        if hasattr(loader, "reset"):
            loader.reset()


def _template_files(engine: Engine, globs: tuple[str, ...]) -> list[tuple[str, Path]]:
    from django.template.utils import get_app_template_dirs

    directories = [Path(d) for d in engine.dirs]
    if engine.app_dirs:
        directories.extend(Path(d) for d in get_app_template_dirs("templates"))

    files: dict[str, Path] = {}
    for directory in directories:
        for file in sorted(directory.rglob("*")):
            if not file.is_file():
                continue
            name = file.relative_to(directory).as_posix()
            if any(fnmatch.fnmatch(name, glob) for glob in globs):
                # First directory wins, matching the filesystem loader's precedence
                files.setdefault(name, file)
    return sorted(files.items())
