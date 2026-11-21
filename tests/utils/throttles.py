"""Every throttle the project defines: the bases in ``core.api.throttling``
and each app's ``throttles`` module."""

from __future__ import annotations

import inspect
from importlib import import_module

from django.apps import apps
from django.utils.module_loading import module_has_submodule
from rest_framework.throttling import SimpleRateThrottle

from core.api import throttling


def throttle_modules() -> list:
    """``core.api.throttling`` and every installed app's ``throttles``."""
    modules = [throttling]
    for app_config in apps.get_app_configs():
        if module_has_submodule(app_config.module, "throttles"):
            modules.append(import_module(f"{app_config.name}.throttles"))
    return modules


def project_throttle_classes() -> dict[str, type[SimpleRateThrottle]]:
    """Each throttle class by name, from the module that defines it."""
    return {
        name: cls
        for module in throttle_modules()
        for name, cls in inspect.getmembers(module, inspect.isclass)
        if issubclass(cls, SimpleRateThrottle)
        and cls.__module__ == module.__name__
    }
