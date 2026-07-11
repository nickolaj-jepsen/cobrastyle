from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.template import engines
from django.template.backends.django import DjangoTemplates
from django.template.backends.jinja2 import Jinja2

from cobrastyle.build import DEFAULT_GLOBS, BuildError, collect_jinja2, emit
from cobrastyle.django import app_config, common_options, dev_resolver, output_dir, static_prefix
from cobrastyle.jinja2 import CobrastyleExtension, configure
from cobrastyle.manager import Stylesheet


class Command(BaseCommand):
    help = "Compile every template's stylesheets into hashed CSS files plus a manifest.json."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--out", help="Output directory (default: COBRASTYLE['OUTPUT_DIR']).")
        parser.add_argument(
            "--url-prefix", help="URL prefix baked into manifest URLs (default: STATIC_URL + 'cobrastyle/')."
        )
        parser.add_argument("--glob", action="append", dest="globs", help="Template glob(s) to build.")
        parser.add_argument(
            "--template",
            action="append",
            dest="extra_templates",
            help="Extra template names the loader cannot enumerate (Jinja2 backend only).",
        )
        parser.add_argument("--strict", action="store_true", help="Fail on any template compile error.")
        parser.add_argument(
            "--clean",
            action="store_true",
            help="First delete previously built files (hashed names and manifest.json; other files survive).",
        )
        parser.add_argument("--no-minify", action="store_true", help="Emit readable CSS instead of minified.")

    def handle(self, *args: Any, **options: Any) -> None:
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

        config = app_config()
        out = Path(options["out"]) if options["out"] else output_dir(config)
        static_url = settings.STATIC_URL or "/static/"
        default_prefix = static_url + (static_prefix(config) or "cobrastyle/")
        url_prefix = options["url_prefix"] or config.get("BUILD_URL_PREFIX", default_prefix)
        globs = tuple(options["globs"] or DEFAULT_GLOBS)
        strict = options["strict"]
        minify = not options["no_minify"]
        resolver = dev_resolver(config)

        pages: dict[str, list[str]] = {}
        stylesheets: dict[str, Stylesheet] = {}
        try:
            if jinja_env is not None:
                # The runtime env may be in prod (manifest) mode; the build always
                # compiles from source, so reconfigure a throwaway overlay into dev mode.
                build_env = jinja_env.overlay()
                build_env.bytecode_cache = None
                configure(build_env, resolver=resolver, **common_options(config))
                collected = collect_jinja2(
                    build_env,
                    globs=globs,
                    strict=strict,
                    extra_templates=tuple(options["extra_templates"] or ()),
                    minify=minify,
                )
                pages.update(collected.pages)
                stylesheets.update({sheet.path: sheet for sheet in collected.stylesheets})

            if dtl_backend is not None:
                from cobrastyle.django.build import collect_dtl

                collected = collect_dtl(
                    dtl_backend, resolver, common_options(config), globs=globs, strict=strict, minify=minify
                )
                pages.update(collected.pages)
                stylesheets.update({sheet.path: sheet for sheet in collected.stylesheets})

            manifest = emit(
                list(stylesheets.values()),
                resolver,
                pages,
                output_dir=out,
                url_prefix=url_prefix,
                clean=options["clean"],
            )
        except BuildError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Built {len(manifest.modules)} module(s) across {len(manifest.pages)} page(s) into {out}"
            )
        )
