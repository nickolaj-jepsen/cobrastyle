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


def find_backends() -> tuple[list[Environment], list[DjangoTemplates]]:
    """Every cobrastyle-configured Jinja2 environment and every DTL backend; CommandError when there are none.

    All of them, not the first of each: a project that keeps a second
    ``DjangoTemplates`` backend (for email, say) would otherwise have those
    templates silently left out of the build. Stricter than dev serving's
    duck-typed discovery on purpose — the commands reconfigure the environment,
    so they only touch backends cobrastyle owns.
    """
    jinja_envs: list[Environment] = []
    dtl_backends: list[DjangoTemplates] = []
    for backend in engines.all():
        if isinstance(backend, Jinja2):
            if CobrastyleExtension.get(backend.env) is not None:
                jinja_envs.append(backend.env)
        elif isinstance(backend, DjangoTemplates):
            dtl_backends.append(backend)
    if not jinja_envs and not dtl_backends:
        raise CommandError(
            "No usable template engine found: configure a Jinja2 backend with "
            "'cobrastyle.django.environment' or a DjangoTemplates backend in TEMPLATES."
        )
    return jinja_envs, dtl_backends


def dev_overlay(jinja_env: Environment, config: CobrastyleSettings, resolver: FileResolver) -> Environment:
    """A throwaway overlay reconfigured into dev mode: builds and checks always
    compile from source, whatever mode the runtime environment is in."""
    overlay = jinja_env.overlay()
    overlay.bytecode_cache = None
    configure(overlay, resolver=resolver, **common_options(config))
    return overlay
