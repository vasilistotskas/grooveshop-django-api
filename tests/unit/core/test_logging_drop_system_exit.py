"""A clean Flower shutdown must not log an ERROR that blocks a promote."""

from __future__ import annotations

import logging
import sys

from core.logging import DropSystemExit


def _record(exc: BaseException) -> logging.LogRecord:
    try:
        raise exc
    except BaseException:
        exc_info = sys.exc_info()
    return logging.LogRecord(
        "tornado.application",
        logging.ERROR,
        __file__,
        1,
        "Uncaught exception",
        None,
        exc_info,
    )


def test_a_system_exit_is_dropped() -> None:
    assert DropSystemExit().filter(_record(SystemExit(0))) is False


def test_any_other_exception_still_logs() -> None:
    assert DropSystemExit().filter(_record(ValueError("real"))) is True


def test_a_record_without_an_exception_still_logs() -> None:
    record = logging.LogRecord(
        "tornado.application", logging.ERROR, __file__, 1, "x", None, None
    )
    assert DropSystemExit().filter(record) is True
