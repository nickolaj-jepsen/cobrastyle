from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from cobrastyle.build import DEFAULT_GLOBS
from cobrastyle.check import UsageCollector, check_jinja2
from cobrastyle.django import app_config, common_options, dev_resolver
from cobrastyle.django.management import dev_overlay, find_backends
from cobrastyle.errors import BuildError


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
        jinja_env, dtl_backend = find_backends()
        config = app_config()
        globs = tuple(options["globs"] or DEFAULT_GLOBS)
        strict = options["strict"]
        resolver = dev_resolver(config)
        collector = UsageCollector()
        try:
            if jinja_env is not None:
                check_env = dev_overlay(jinja_env, config, resolver)
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
