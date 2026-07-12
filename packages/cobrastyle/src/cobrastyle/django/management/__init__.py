from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.management.base import CommandError
from django.template import engines
from django.template.backends.django import DjangoTemplates
from django.template.backends.jinja2 import Jinja2

from cobrastyle.django import CobrastyleSettings, common_options
from cobrastyle.jinja2 import CobrastyleExtension, configure

if TYPE_CHECKING:
    from jinja2 import Environment

    from cobrastyle.resolvers import FileResolver


def find_backends() -> tuple[Environment | None, DjangoTemplates | None]:
    """The first cobrastyle-configured Jinja2 backend's environment and the first
    DTL backend; CommandError when neither exists.

    Stricter than dev serving's duck-typed discovery on purpose: the commands
    reconfigure the environment, so they only touch backends cobrastyle owns.
    """
    jinja_env = None
    dtl_backend = None
    for backend in engines.all():
        if isinstance(backend, Jinja2) and jinja_env is None:
            if CobrastyleExtension.get(backend.env) is not None:
                jinja_env = backend.env
        elif isinstance(backend, DjangoTemplates) and dtl_backend is None:
            dtl_backend = backend
    if jinja_env is None and dtl_backend is None:
        raise CommandError(
            "No usable template engine found: configure a Jinja2 backend with "
            "'cobrastyle.django.environment' or a DjangoTemplates backend in TEMPLATES."
        )
    return jinja_env, dtl_backend


def dev_overlay(jinja_env: Environment, config: CobrastyleSettings, resolver: FileResolver) -> Environment:
    """A throwaway overlay reconfigured into dev mode: builds and checks always
    compile from source, whatever mode the runtime environment is in."""
    overlay = jinja_env.overlay()
    overlay.bytecode_cache = None
    configure(overlay, resolver=resolver, **common_options(config))
    return overlay
