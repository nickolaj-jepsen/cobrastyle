from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.template import engines
from django.template.backends.django import DjangoTemplates
from django.template.backends.jinja2 import Jinja2

from cobrastyle.build import DEFAULT_GLOBS, BuildError
from cobrastyle.check import UsageCollector, check_jinja2
from cobrastyle.django import app_config, common_options, dev_resolver
from cobrastyle.jinja2 import CobrastyleExtension, configure


class Command(BaseCommand):
    help = "Validate template class references against module exports and report unused exports."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--glob", action="append", dest="globs", help="Template glob(s) to check.")
        parser.add_argument(
            "--template",
            action="append",
            dest="extra_templates",
            help="Extra template names the loader cannot enumerate (Jinja2 backend only).",
        )
        parser.add_argument("--strict", action="store_true", help="Fail on any template compile error.")
        parser.add_argument(
            "--strict-unused",
            action="store_true",
            dest="strict_unused",
            help="Exit non-zero when unused classes are reported.",
        )

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
        globs = tuple(options["globs"] or DEFAULT_GLOBS)
        strict = options["strict"]
        resolver = dev_resolver(config)
        collector = UsageCollector()
        try:
            if jinja_env is not None:
                # The runtime env may be in prod (manifest) mode; the check always
                # compiles from source, so reconfigure a throwaway overlay into dev mode.
                check_env = jinja_env.overlay()
                check_env.bytecode_cache = None
                configure(check_env, resolver=resolver, **common_options(config))
                check_jinja2(
                    check_env,
                    collector,
                    globs=globs,
                    strict=strict,
                    extra_templates=tuple(options["extra_templates"] or ()),
                )

            if dtl_backend is not None:
                from cobrastyle.django.check import check_dtl

                check_dtl(dtl_backend, resolver, common_options(config), collector, globs=globs, strict=strict)
        except BuildError as exc:
            raise CommandError(str(exc)) from exc

        report = collector.report()
        for line in report.error_lines():
            self.stderr.write(line)
        for line in report.warning_lines():
            self.stderr.write(self.style.WARNING(line))
        failures = report.failures(strict_unused=options["strict_unused"])
        if failures:
            raise CommandError(f"{report.summary()}: {', '.join(failures)}")
        self.stdout.write(self.style.SUCCESS(f"{report.summary()}: OK"))
