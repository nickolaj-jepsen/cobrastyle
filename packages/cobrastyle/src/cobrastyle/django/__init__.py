from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.templatetags.static import static
from django.urls import reverse
from jinja2 import Environment

from cobrastyle.jinja2 import CobrastyleExtension, ConfigureOptions, configure, extended
from cobrastyle.manifest import Manifest, ModuleEntry
from cobrastyle.resolvers import FileSystemResolver

if TYPE_CHECKING:
    from collections.abc import Callable


class CobrastyleSettings(TypedDict, total=False):
    """The shape of the ``COBRASTYLE`` settings dict."""

    DEV: bool
    ROOT: str | Path
    URL_PREFIX: str
    MANIFEST: Manifest | str | Path
    OUTPUT_DIR: str | Path
    BUILD_URL_PREFIX: str
    STATIC_PREFIX: str | None
    MINIFY: bool
    REWRITE_CLASS_NAMES: bool
    MODULE_PATTERN: str | None
    TARGETS: list[str] | None
    SOURCE_MAP: bool


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
        configure(env, manifest=manifest, url_map=static_url_map(config), **common)


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
    if "SOURCE_MAP" in config:
        options["source_map"] = config["SOURCE_MAP"]
    return options


def dev_resolver(config: CobrastyleSettings) -> FileSystemResolver:
    root = config.get("ROOT")
    if root is None:
        root = _base_dir("COBRASTYLE['ROOT']") / "styles"
    return FileSystemResolver(root, url_prefix=config.get("URL_PREFIX", "/cobrastyle/"))


def static_prefix(config: CobrastyleSettings) -> str | None:
    """The static name prefix built files live under, slash-terminated; None disables staticfiles integration."""
    prefix = config.get("STATIC_PREFIX", "cobrastyle/")
    if prefix is None:
        return None
    return prefix if prefix.endswith("/") else prefix + "/"


def static_url_map(config: CobrastyleSettings) -> Callable[[ModuleEntry], str] | None:
    """The prod URL mapping through Django's static() — hashed storages and CDN
    hosts apply. None (baked manifest URLs) when disabled via ``STATIC_PREFIX``
    or overridden with an explicit ``BUILD_URL_PREFIX``."""
    prefix = static_prefix(config)
    if prefix is None or "BUILD_URL_PREFIX" in config:
        return None
    return lambda entry: static(prefix + entry.file)


def output_dir(config: CobrastyleSettings) -> Path:
    out = config.get("OUTPUT_DIR")
    if out is None:
        out = _base_dir("COBRASTYLE['OUTPUT_DIR']") / "cobrastyle_static" / "cobrastyle"
    return Path(out)


# ManifestStaticFilesStorage's hashed names — WhiteNoise's storage round-trip
# already recognizes them, but a custom test replaces it wholesale
_DJANGO_HASHED_NAME = re.compile(r"\.[0-9a-f]{12}\.")


def immutable_file_test(path: str, url: str) -> bool:
    """``WHITENOISE_IMMUTABLE_FILE_TEST`` hook marking content-hashed files cacheable forever.

    Matches cobrastyle's hashed names under the configured static prefix —
    needed under plain static storage, where WhiteNoise's default test only
    recognizes names hashed by the storage itself — plus Django's 12-hex
    hashed names, so other assets keep their immutable headers too.
    """
    from urllib.parse import urlparse

    from cobrastyle.build import HASHED_NAME

    prefix = static_prefix(app_config())
    if prefix is not None:
        # WhiteNoise passes a URL path — compare against STATIC_URL's path so
        # absolute/CDN STATIC_URL settings still match
        static_url = urlparse(settings.STATIC_URL or "/static/").path
        if url.startswith(static_url + prefix) and HASHED_NAME.search(url):
            return True
    return bool(_DJANGO_HASHED_NAME.search(url))


def _base_dir(needed_for: str) -> Path:
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir is None:
        raise ImproperlyConfigured(f"Set {needed_for} explicitly, or define BASE_DIR in settings.")
    return Path(base_dir)
