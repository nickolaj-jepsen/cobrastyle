from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

from cobrastyle.build import DEFAULT_GLOBS, CollectedTemplates, handle_compile_failure
from cobrastyle.django.runtime import DTLRuntime, get_runtime, set_runtime
from cobrastyle.jinja2 import ConfigureOptions
from cobrastyle.manager import CobrastyleManager
from cobrastyle.resolvers import FileResolver

if TYPE_CHECKING:
    from django.template.backends.django import DjangoTemplates
    from django.template.engine import Engine


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
    # The build emits production CSS — minified unless told otherwise, never
    # source maps — whatever the dev-serving options say.
    options: ConfigureOptions = {**manager_options, "minify": minify, "source_map": False}
    manager = CobrastyleManager(resolver, analyze_dependencies=True, **options)
    runtime = DTLRuntime(manager=manager)
    set_runtime(runtime)
    try:
        # Django caches parsed templates even in DEBUG (4.1+); {% cobrastyle %}
        # fires at parse time, so anything rendered earlier in this process
        # would silently drop out of the build without a reset.
        for loader in backend.engine.template_loaders:
            if hasattr(loader, "reset"):
                loader.reset()
        for name, file in _template_files(backend.engine, globs):
            try:
                backend.get_template(name)
            except Exception as exc:
                handle_compile_failure(name, file.read_text(errors="replace"), exc, strict)
    finally:
        set_runtime(None)

    pages = {
        runtime.page_names[origin_name]: paths
        for origin_name, paths in runtime.pages.items()
        if paths and origin_name in runtime.page_names
    }
    return CollectedTemplates(pages, manager.stylesheets, resolver)


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


__all__ = ["collect_dtl", "get_runtime"]
