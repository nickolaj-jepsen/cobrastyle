from __future__ import annotations

import fnmatch
import hashlib
import logging
import posixpath
import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, NamedTuple

from jinja2 import Environment

from cobrastyle.errors import BuildError
from cobrastyle.jinja2 import CobrastyleExtension, extended
from cobrastyle.manifest import AssetEntry, Manifest, ModuleEntry
from cobrastyle.paths import normalize_path

if TYPE_CHECKING:
    from collections.abc import Iterator

    from cobrastyle.manager import Stylesheet
    from cobrastyle.resolvers import FileResolver

logger = logging.getLogger(__name__)

DEFAULT_GLOBS = ("*.html", "*.jinja", "*.jinja2", "*.j2")

# lightningcss's own default: compact, unlike the readable dev DEV_MODULE_PATTERN
BUILD_MODULE_PATTERN = "[hash]_[local]"

# scheme:, protocol-relative, or same-document fragment — passed through untouched.
# Must agree with is_external in cobrastyle-lightningcss/src/lib.rs: the bundler
# decides which @imports stay external, this regex re-classifies the survivors
# (test_external_url_classification_matches_the_bundler pins the agreement).
_EXTERNAL_URL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#)", re.IGNORECASE)


class CollectedTemplates(NamedTuple):
    """What a template walk produced, ready for :func:`emit`."""

    pages: dict[str, list[str]]
    stylesheets: list[Stylesheet]
    resolver: FileResolver | None


def handle_compile_failure(name: str, source: str, exc: Exception, strict: bool) -> None:
    """Raise :class:`BuildError` when the template matters (decision: strict, or it mentions cobrastyle);
    otherwise log the skip so nothing is dropped silently."""
    if strict or "cobrastyle" in source:
        raise BuildError(f"Failed to compile template {name!r}: {exc}") from exc
    logger.warning("Skipping template %s (does not use cobrastyle): %s", name, exc)


def build(
    environment: Environment,
    *,
    output_dir: str | Path,
    url_prefix: str = "/static/",
    globs: tuple[str, ...] = DEFAULT_GLOBS,
    strict: bool = False,
    extra_templates: tuple[str, ...] = (),
    clean: bool = False,
    minify: bool = True,
) -> Manifest:
    """Compile every template's stylesheets and write hashed CSS + ``manifest.json`` to ``output_dir``.

    Templates are enumerated via ``list_templates()`` filtered by ``globs``,
    plus ``extra_templates`` (the escape hatch for loaders that can't
    enumerate). Compilation bypasses both template caches so extension hooks
    always run. A template that fails to compile is skipped with a warning
    unless it mentions cobrastyle or ``strict`` is set — then it's a
    :class:`BuildError`. Output is deterministic: identical input produces
    byte-identical files. ``clean`` removes previously built files first;
    see :func:`emit`. ``minify`` applies regardless of the environment's
    dev-serving configuration.
    """
    collected = collect_jinja2(environment, globs=globs, strict=strict, extra_templates=extra_templates, minify=minify)
    return emit(
        collected.stylesheets,
        collected.resolver,
        collected.pages,
        output_dir=output_dir,
        url_prefix=url_prefix,
        clean=clean,
    )


def overlay_with_extension(environment: Environment) -> tuple[Environment, CobrastyleExtension]:
    """An overlay of ``environment`` plus its bound extension, validated for a template walk."""
    overlay = environment.overlay()
    extension = CobrastyleExtension.get(overlay)
    if extension is None:
        raise BuildError("CobrastyleExtension is not registered on the environment")
    if overlay.loader is None:
        raise BuildError("The environment has no loader; there are no templates to process")
    return overlay, extension


def walk_template_sources(
    environment: Environment, globs: tuple[str, ...], extra_templates: tuple[str, ...]
) -> Iterator[tuple[str, str, str | None]]:
    """Yield (name, source, filename) for every matching template; BuildError when one cannot load."""
    loader = environment.loader
    assert loader is not None  # overlay_with_extension already rejected loaderless environments
    for name in _enumerate(environment, globs, extra_templates):
        try:
            source, filename, _ = loader.get_source(environment, name)
        except Exception as exc:
            raise BuildError(f"Cannot load template {name!r}: {exc}") from exc
        yield name, source, filename


def collect_jinja2(
    environment: Environment,
    *,
    globs: tuple[str, ...] = DEFAULT_GLOBS,
    strict: bool = False,
    extra_templates: tuple[str, ...] = (),
    minify: bool = True,
) -> CollectedTemplates:
    """Walk the environment's templates; return the pages, compiled stylesheets, and their resolver."""
    build_env, extension = overlay_with_extension(environment)
    if extended(build_env).cobrastyle_manifest is not None:
        raise BuildError(
            "The environment is configured in manifest (prod) mode; the build compiles from source. "
            "Point the build at a dev-configured target (configure(resolver=...))."
        )
    # Force a fresh manager with dependency analysis on (url()/@import become placeholders
    # emit() resolves); build output is production CSS regardless of dev-serving settings
    extended(build_env).cobrastyle_analyze_dependencies = True
    extended(build_env).cobrastyle_minify = minify
    extended(build_env).cobrastyle_source_map = False
    if extended(build_env).cobrastyle_module_pattern is None:
        extended(build_env).cobrastyle_module_pattern = BUILD_MODULE_PATTERN
    extension._manager = None

    pages: dict[str, list[str]] = {}
    for name, source, filename in walk_template_sources(build_env, globs, extra_templates):
        try:
            # Full compile(), not parse(): codegen-stage errors (unknown filters,
            # duplicate blocks) must fail the build per handle_compile_failure's rules.
            build_env.compile(source, name=name, filename=filename)
        except Exception as exc:
            handle_compile_failure(name, source, exc, strict)
            continue
        if modules := extension.page_modules(name):
            pages[name] = modules

    manager = extension._manager
    if manager is None:
        return CollectedTemplates(pages, [], None)
    return CollectedTemplates(pages, manager.stylesheets, manager.resolver)


def emit(
    stylesheets: list[Stylesheet],
    resolver: FileResolver | None,
    pages: dict[str, list[str]],
    *,
    output_dir: str | Path,
    url_prefix: str = "/static/",
    clean: bool = False,
) -> Manifest:
    """Write hashed CSS files, url() assets and ``manifest.json`` to ``output_dir``.

    ``clean`` first deletes previously built files — hashed names and
    ``manifest.json`` only, so user files sharing the directory survive.
    Leave it off when old hashes must outlive a rolling deploy.
    """
    prefix = url_prefix if url_prefix.endswith("/") else url_prefix + "/"
    output = Path(output_dir)
    if clean:
        _clean_output(output)
    manifest = Manifest(generator=_generator_versions(), pages=pages)

    for stylesheet in sorted(stylesheets, key=lambda s: s.path):
        code, module_assets = _resolve_assets(stylesheet, resolver, manifest, output, prefix)
        data = code.encode()
        hashed = _hashed_name(stylesheet.path, data)
        target = output / hashed
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest.modules[stylesheet.path] = ModuleEntry(
            file=hashed,
            url=prefix + hashed,
            classes=stylesheet.classes,
            assets=module_assets,
        )

    output.mkdir(parents=True, exist_ok=True)
    manifest.dump(output / "manifest.json")
    return manifest


def _hashed_name(path: str, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()[:10]
    source_path = PurePosixPath(path)
    return str(source_path.parent / f"{source_path.stem}.{digest}{source_path.suffix}")


# What _hashed_name produces — the naming contract consumers (cleanup,
# cache-header helpers) match against
HASHED_NAME = re.compile(r"\.[0-9a-f]{10}\.\w+$")


def _clean_output(output: Path) -> None:
    if not output.is_dir():
        return
    (output / "manifest.json").unlink(missing_ok=True)
    # A parent sorts before its children, so the reverse walk empties directories bottom-up
    for path in sorted(output.rglob("*"), reverse=True):
        if path.is_file():
            if HASHED_NAME.search(path.name):
                path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def _resolve_assets(
    stylesheet: Stylesheet, resolver: FileResolver | None, manifest: Manifest, output: Path, prefix: str
) -> tuple[str, list[str]]:
    """Emit hashed copies of the module's url() assets and substitute their placeholders.

    Substituted references are relative to the module's own output location,
    so the built CSS works wherever it is served from (any URL prefix, CDN
    host, or static-file pipeline). The manifest still records absolute URLs.
    """
    from cobrastyle_lightningcss import Dependency

    code = stylesheet.code
    module_assets: list[str] = []
    for dependency in stylesheet.dependencies:
        if isinstance(dependency, Dependency.Import):
            if not _EXTERNAL_URL.match(dependency.url):
                # The bundler inlines every non-external @import before the build sees it
                raise BuildError(f"Unresolvable @import of {dependency.url!r} in {stylesheet.path!r}")
            code = code.replace(dependency.placeholder, dependency.url)
            continue
        assert isinstance(dependency, Dependency.Url)
        if _EXTERNAL_URL.match(dependency.url):
            code = code.replace(dependency.placeholder, dependency.url)
            continue

        bare_url = dependency.url.split("?", 1)[0].split("#", 1)[0]
        extra = dependency.url[len(bare_url) :]
        # Relative to the file that wrote the url() — with bundled @imports
        # that is not necessarily the entry module
        try:
            asset_path = normalize_path(posixpath.join(posixpath.dirname(dependency.loc.file_path), bare_url))
        except ValueError as exc:
            raise BuildError(f"Asset {dependency.url!r} in {stylesheet.path!r} escapes the resolver root") from exc

        if asset_path not in manifest.assets:
            # A stylesheet with local url() dependencies was compiled by a manager, which has a resolver
            assert resolver is not None
            try:
                data = resolver.read_bytes(asset_path)
            except (KeyError, OSError) as exc:
                raise BuildError(f"Asset {asset_path!r} referenced by {stylesheet.path!r} not found: {exc}") from exc
            hashed = _hashed_name(asset_path, data)
            target = output / hashed
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            manifest.assets[asset_path] = AssetEntry(file=hashed, url=prefix + hashed)

        # Hashed names keep their source directory, so the module's final
        # directory is known before its content hash is
        relative = posixpath.relpath(manifest.assets[asset_path].file, start=posixpath.dirname(stylesheet.path) or ".")
        code = code.replace(dependency.placeholder, relative + extra)
        if asset_path not in module_assets:
            module_assets.append(asset_path)
    return code, module_assets


def _enumerate(environment: Environment, globs: tuple[str, ...], extra_templates: tuple[str, ...]) -> list[str]:
    try:
        names = environment.list_templates()
    except TypeError as exc:
        if not extra_templates:
            raise BuildError(
                f"This loader cannot enumerate templates ({exc}); pass extra_templates with explicit names."
            ) from exc
        names = []
    matched = [name for name in names if any(fnmatch.fnmatch(name, glob) for glob in globs)]
    for name in extra_templates:
        if name not in matched:
            matched.append(name)
    return matched


def _generator_versions() -> dict[str, str]:
    import cobrastyle_lightningcss

    try:
        own_version = package_version("cobrastyle")
    except PackageNotFoundError:
        own_version = "unknown"
    return {
        "cobrastyle": own_version,
        "lightningcss": getattr(cobrastyle_lightningcss, "LIGHTNINGCSS_VERSION", "unknown"),
    }
