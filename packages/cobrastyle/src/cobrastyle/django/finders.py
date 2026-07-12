from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from django.contrib.staticfiles import utils
from django.contrib.staticfiles.finders import BaseFinder
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files.storage import FileSystemStorage
from django.utils._os import safe_join

from cobrastyle.django import app_config, output_dir, static_prefix

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

MANIFEST_NAME = "manifest.json"


class _PrefixedStorage(FileSystemStorage):
    """collectstatic places files under ``storage.prefix``, exactly like a prefixed STATICFILES_DIRS entry."""

    prefix: str


class CobrastyleFinder(BaseFinder):
    """Static files finder exposing the build output under the configured static prefix.

    Add ``"cobrastyle.django.finders.CobrastyleFinder"`` to
    ``STATICFILES_FINDERS`` (alongside the defaults) and ``collectstatic``
    picks up everything ``manage.py cobrastyle_build`` wrote — except
    ``manifest.json``, which is the build→runtime contract the app reads
    from ``OUTPUT_DIR``, not a public asset.
    """

    def __init__(self, app_names: Any = None, *args: Any, **kwargs: Any):
        config = app_config()
        prefix = static_prefix(config)
        if prefix is None:
            raise ImproperlyConfigured(
                "CobrastyleFinder is listed in STATICFILES_FINDERS but COBRASTYLE['STATIC_PREFIX'] "
                "is None; set a prefix or remove the finder."
            )
        self.prefix = prefix
        self.root = output_dir(config)
        self.storage = _PrefixedStorage(location=self.root)
        self.storage.prefix = prefix.rstrip("/")
        super().__init__(*args, **kwargs)

    def check(self, **kwargs: Any) -> list[Any]:
        return []

    def find(self, path: str, find_all: bool = False, **kwargs: Any) -> Any:
        match = self._find(path)
        if find_all:
            return [match] if match else []
        return match

    def _find(self, path: str) -> str | None:
        if not path.startswith(self.prefix):
            return None
        relative = path.removeprefix(self.prefix)
        if relative == MANIFEST_NAME:
            return None
        try:
            full = safe_join(str(self.root), relative)
        except (ValueError, SuspiciousFileOperation):
            return None
        return full if os.path.exists(full) else None

    def list(self, ignore_patterns: Iterable[str] | None) -> Iterator[tuple[str, FileSystemStorage]]:
        if not self.root.is_dir():
            # Nothing built yet; collectstatic before cobrastyle_build collects no CSS
            return
        for path in utils.get_files(self.storage, ignore_patterns):
            if path != MANIFEST_NAME:
                yield path, self.storage
