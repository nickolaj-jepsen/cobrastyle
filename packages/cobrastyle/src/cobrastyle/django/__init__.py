from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.templatetags.static import static
from django.urls import reverse
from jinja2 import Environment

from cobrastyle.jinja2 import CobrastyleExtension, ConfigureOptions, configure, extended
from cobrastyle.manifest import Manifest
from cobrastyle.resolvers import FileSystemResolver


class CobrastyleSettings(TypedDict, total=False):
    """The shape of the ``COBRASTYLE`` settings dict."""

    DEV: bool
    ROOT: str | Path
    URL_PREFIX: str
    MANIFEST: Manifest | str | Path
    OUTPUT_DIR: str | Path
    BUILD_URL_PREFIX: str
    MINIFY: bool
    REWRITE_CLASS_NAMES: bool
    MODULE_PATTERN: str | None
    TARGETS: list[str] | None


def environment(**options: Any) -> Environment:
    """Jinja2 environment factory for Django's ``TEMPLATES`` ``OPTIONS["environment"]``.

    Builds the environment with :class:`CobrastyleExtension`, the usual
    ``static``/``url`` globals, and cobrastyle configured from the
    ``COBRASTYLE`` settings dict: dev mode (compile on demand) when
    ``settings.DEBUG`` (override with ``COBRASTYLE["DEV"]``), otherwise prod
    mode reading the built manifest.
    """
    extensions = list(options.pop("extensions", ()))
    if CobrastyleExtension not in extensions:
        extensions.append(CobrastyleExtension)
    env = Environment(extensions=extensions, **options)
    extended(env).globals["static"] = static
    extended(env).globals["url"] = reverse
    configure_from_settings(env)
    return env


def configure_from_settings(env: Environment) -> None:
    """Apply the ``COBRASTYLE`` settings dict to an environment; see :func:`environment`."""
    config = app_config()
    common = common_options(config)
    if config.get("DEV", settings.DEBUG):
        configure(env, resolver=dev_resolver(config), **common)
    else:
        manifest = config.get("MANIFEST", output_dir(config) / "manifest.json")
        configure(env, manifest=manifest, **common)


def app_config() -> CobrastyleSettings:
    return getattr(settings, "COBRASTYLE", CobrastyleSettings())


def common_options(config: CobrastyleSettings) -> ConfigureOptions:
    options: ConfigureOptions = {}
    if "MINIFY" in config:
        options["minify"] = config["MINIFY"]
    if "REWRITE_CLASS_NAMES" in config:
        options["rewrite_class_names"] = config["REWRITE_CLASS_NAMES"]
    if "MODULE_PATTERN" in config:
        options["module_pattern"] = config["MODULE_PATTERN"]
    if "TARGETS" in config:
        options["targets"] = config["TARGETS"]
    return options


def dev_resolver(config: CobrastyleSettings) -> FileSystemResolver:
    root = config.get("ROOT")
    if root is None:
        root = _base_dir("COBRASTYLE['ROOT']") / "styles"
    return FileSystemResolver(root, url_prefix=config.get("URL_PREFIX", "/cobrastyle/"))


def output_dir(config: CobrastyleSettings) -> Path:
    out = config.get("OUTPUT_DIR")
    if out is None:
        out = _base_dir("COBRASTYLE['OUTPUT_DIR']") / "cobrastyle_static" / "cobrastyle"
    return Path(out)


def _base_dir(needed_for: str) -> Path:
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir is None:
        raise ImproperlyConfigured(f"Set {needed_for} explicitly, or define BASE_DIR in settings.")
    return Path(base_dir)
