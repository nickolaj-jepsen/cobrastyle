from django.conf import settings
from django.http import HttpRequest
from django.utils.functional import lazy

from cobrastyle_core import CobrastyleManager


def cobrastyle(request: HttpRequest) -> dict[str, CobrastyleManager]:
    resolver = getattr(settings, "COBRASTYLE_RESOLVER", None)
    if resolver is None:
        raise ValueError("COBRASTYLE_RESOLVER is not set in settings")

    # get all settings starting with COBRASTYLE_
    all_settings = dir(settings)
    resolver_settings = {
        setting[11:].lower(): getattr(settings, setting)
        for setting in all_settings
        if setting.startswith("COBRASTYLE_") and setting != "COBRASTYLE_RESOLVER"
    }

    manager = CobrastyleManager(resolver, **resolver_settings)

    return {
        "cobrastyle": manager,
        "used_stylesheets": lazy(manager.used_stylesheets, list),
    }