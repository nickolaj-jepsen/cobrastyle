from __future__ import annotations

import importlib
import sys
from pathlib import Path

import click
from jinja2 import Environment

from cobrastyle.build import DEFAULT_GLOBS, BuildError
from cobrastyle.build import build as run_build


def adapt_target(obj: object) -> Environment:
    """Extract a jinja2 Environment from ``obj``.

    Accepts a bare Environment, a Flask app (``.jinja_env``), a Starlette/
    FastAPI ``Jinja2Templates`` (``.env``), or a zero-arg factory returning
    any of these.
    """
    environment = _extract_environment(obj)
    if environment is not None:
        return environment
    if callable(obj):
        produced = obj()
        environment = _extract_environment(produced)
        if environment is not None:
            return environment
        if callable(produced):
            raise click.ClickException(
                f"The factory returned another callable ({produced!r}); expected an environment or app."
            )
        obj = produced
    raise click.ClickException(
        f"Cannot adapt {obj!r} (type {type(obj).__name__}) to a jinja2.Environment. "
        "Pass an Environment, a Flask app, a Jinja2Templates instance, or a zero-arg factory returning one."
    )


def _extract_environment(obj: object) -> Environment | None:
    if isinstance(obj, Environment):
        return obj
    for attribute in ("jinja_env", "env"):
        candidate = getattr(obj, attribute, None)
        if isinstance(candidate, Environment):
            return candidate
    return None


def import_target(spec: str) -> object:
    """Import ``package.module:attribute`` (attribute may be dotted)."""
    module_name, sep, attribute_path = spec.partition(":")
    if not sep or not module_name or not attribute_path:
        raise click.UsageError(f"Target must look like 'package.module:attribute', got {spec!r}")
    try:
        obj: object = importlib.import_module(module_name)
    except ImportError as exc:
        raise click.ClickException(f"Cannot import module {module_name!r}: {exc}") from exc
    for part in attribute_path.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError as exc:
            raise click.ClickException(f"Module {module_name!r} has no attribute {attribute_path!r}") from exc
    return obj


@click.group()
def cli() -> None:
    """CSS modules for Python template engines."""


@cli.command("build")
@click.argument("target")
@click.option(
    "--out",
    "-o",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default="cobrastyle_static",
    show_default=True,
    help="Directory receiving hashed CSS files and manifest.json.",
)
@click.option("--url-prefix", default="/static/", show_default=True, help="URL prefix baked into manifest URLs.")
@click.option(
    "--glob", "globs", multiple=True, help=f"Template glob(s) to build. [default: {', '.join(DEFAULT_GLOBS)}]"
)
@click.option("--template", "extra_templates", multiple=True, help="Extra template names the loader cannot enumerate.")
@click.option("--strict", is_flag=True, help="Fail on any template compile error, cobrastyle or not.")
@click.option(
    "--clean",
    is_flag=True,
    help="First delete previously built files (hashed names and manifest.json; other files survive).",
)
@click.option("--no-minify", is_flag=True, help="Emit readable CSS instead of minified.")
def build_command(
    target: str,
    output_dir: Path,
    url_prefix: str,
    globs: tuple[str, ...],
    extra_templates: tuple[str, ...],
    strict: bool,
    clean: bool,
    no_minify: bool,
) -> None:
    """Build production CSS and manifest for TARGET (package.module:attribute)."""
    if "" not in sys.path and "." not in sys.path:
        sys.path.insert(0, "")
    environment = adapt_target(import_target(target))
    try:
        manifest = run_build(
            environment,
            output_dir=output_dir,
            url_prefix=url_prefix,
            globs=globs or DEFAULT_GLOBS,
            strict=strict,
            extra_templates=extra_templates,
            clean=clean,
            minify=not no_minify,
        )
    except BuildError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Built {len(manifest.modules)} module(s) across {len(manifest.pages)} page(s) into {output_dir}")


@cli.command("check")
@click.argument("target")
@click.option(
    "--glob", "globs", multiple=True, help=f"Template glob(s) to check. [default: {', '.join(DEFAULT_GLOBS)}]"
)
@click.option("--template", "extra_templates", multiple=True, help="Extra template names the loader cannot enumerate.")
@click.option("--strict", is_flag=True, help="Fail on any template compile error, cobrastyle or not.")
@click.option("--strict-unused", is_flag=True, help="Exit non-zero when unused classes are reported.")
def check_command(
    target: str,
    globs: tuple[str, ...],
    extra_templates: tuple[str, ...],
    strict: bool,
    strict_unused: bool,
) -> None:
    """Validate template class references for TARGET (package.module:attribute).

    Errors on references to classes a module does not export; warns about
    exported classes no template references (best-effort — dynamic access
    disables the unused report for that module). Class maps resolve the way
    the target is configured: from source in dev mode, from the built
    manifest in prod mode — point CI at a dev-configured target to check
    without a build.
    """
    from cobrastyle.check import UsageCollector, check_jinja2

    if "" not in sys.path and "." not in sys.path:
        sys.path.insert(0, "")
    environment = adapt_target(import_target(target))
    collector = UsageCollector()
    try:
        check_jinja2(
            environment, collector, globs=globs or DEFAULT_GLOBS, strict=strict, extra_templates=extra_templates
        )
    except BuildError as exc:
        raise click.ClickException(str(exc)) from exc
    report = collector.report()
    for line in report.error_lines():
        click.echo(line, err=True)
    for line in report.warning_lines():
        click.echo(line, err=True)
    failures = report.failures(strict_unused=strict_unused)
    if failures:
        click.echo(f"{report.summary()}: {', '.join(failures)}")
        raise SystemExit(1)
    click.echo(f"{report.summary()}: OK")
