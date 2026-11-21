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
    """Each throttle class by name, from the module that defines it.

    Two modules defining one name fail here: the second would hide the
    first from every check that reads this map.
    """
    found: dict[str, type[SimpleRateThrottle]] = {}
    for module in throttle_modules():
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if not issubclass(cls, SimpleRateThrottle):
                continue
            if cls.__module__ != module.__name__:
                continue
            if name in found:
                raise AssertionError(
                    f"Throttle {name} is defined in both "
                    f"{found[name].__module__} and {module.__name__}"
                )
            found[name] = cls
    return found
