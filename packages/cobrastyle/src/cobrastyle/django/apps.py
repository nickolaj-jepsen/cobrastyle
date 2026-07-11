from typing import Any

from django.apps import AppConfig
from django.test.signals import setting_changed


class CobrastyleConfig(AppConfig):
    name = "cobrastyle.django"
    label = "cobrastyle"
    verbose_name = "Cobrastyle"

    def ready(self) -> None:
        setting_changed.connect(_reset_runtime_on_setting_change)


def _reset_runtime_on_setting_change(*, setting: str, **kwargs: Any) -> None:
    if setting in ("COBRASTYLE", "DEBUG", "BASE_DIR"):
        from cobrastyle.django.runtime import set_runtime

        set_runtime(None)
