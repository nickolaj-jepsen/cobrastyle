from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.templatetags.static import static
from django.urls import reverse
from jinja2 import Environment

from cobrastyle.jinja2 import CobrastyleExtension, configure, extended
from cobrastyle.resolvers import FileSystemResolver

_OPTION_KEYS = {
    "MINIFY": "minify",
    "REWRITE_CLASS_NAMES": "rewrite_class_names",
    "MODULE_PATTERN": "module_pattern",
    "TARGETS": "targets",
}


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


def app_config() -> dict[str, Any]:
    return getattr(settings, "COBRASTYLE", {})


def common_options(config: dict[str, Any]) -> dict[str, Any]:
    return {option: config[key] for key, option in _OPTION_KEYS.items() if key in config}


def dev_resolver(config: dict[str, Any]) -> FileSystemResolver:
    root = config.get("ROOT")
    if root is None:
        root = _base_dir("COBRASTYLE['ROOT']") / "styles"
    return FileSystemResolver(root, url_prefix=config.get("URL_PREFIX", "/cobrastyle/"))


def output_dir(config: dict[str, Any]) -> Path:
    out = config.get("OUTPUT_DIR")
    if out is None:
        out = _base_dir("COBRASTYLE['OUTPUT_DIR']") / "cobrastyle_static" / "cobrastyle"
    return Path(out)


def _base_dir(needed_for: str) -> Path:
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir is None:
        raise ImproperlyConfigured(f"Set {needed_for} explicitly, or define BASE_DIR in settings.")
    return Path(base_dir)
