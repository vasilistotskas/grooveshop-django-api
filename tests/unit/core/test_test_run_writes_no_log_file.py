"""The pytest run writes no log file.

``tests/settings.py`` pins ``DEBUG`` on, which makes ``settings.py`` add a
``RotatingFileHandler`` every xdist worker would open. On Windows the
rotation of that shared file fails in one worker while the others hold it
open, and ``logging`` prints the error to stderr.
"""

import logging
from importlib import import_module

from django.conf import settings


def _handler_class(dotted: str) -> type:
    module, _, name = dotted.rpartition(".")
    return getattr(import_module(module), name)


def test_the_logging_config_has_no_file_handler():
    file_handlers = [
        name
        for name, config in settings.LOGGING["handlers"].items()
        if issubclass(_handler_class(config["class"]), logging.FileHandler)
    ]
    assert file_handlers == []


def test_the_root_logger_uses_only_declared_handlers():
    assert set(settings.LOGGING["root"]["handlers"]) <= set(
        settings.LOGGING["handlers"]
    )
