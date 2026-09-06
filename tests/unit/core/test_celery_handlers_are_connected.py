"""A Celery signal handler defined in a function needs `weak=False`.

The six handlers in `create_celery_app()` are closures. Under Celery's
default weak references the only strong reference to each dies when that
function returns, so every receiver was garbage-collected before a
worker ever started:

    before_task_publish        has_listeners=False
    task_prerun                has_listeners=False
    task_postrun               has_listeners=False
    worker_process_init        has_listeners=False
    worker_process_shutdown    has_listeners=False

A signal that never fires raises nothing, which is why this held. The
consequences were silent: `apply_db_overlay()` never ran at worker boot,
so a worker only ever saw the `.mo` values baked into the image and
stayed blind to every Rosetta save — exactly the "Order Received - #38
shipped in English" failure the handler's own comment claims to have
fixed. Correlation ids never reached worker logs either.

`meili/apps.py` documents the identical trap for its own closures, with
a comment explaining why `weak=False` is required there.
"""

from __future__ import annotations

import pytest
from celery import signals

_HANDLED = [
    "before_task_publish",
    "task_prerun",
    "task_postrun",
    "worker_process_init",
    "worker_process_shutdown",
]


@pytest.mark.parametrize("signal_name", _HANDLED)
def test_the_handler_survives_app_creation(signal_name):
    signal = getattr(signals, signal_name)

    assert signal.has_listeners(), (
        f"{signal_name} has no live receiver — the closure in "
        f"create_celery_app() was garbage-collected. Connect it with "
        f"weak=False."
    )


def test_setup_logging_is_module_level_and_unaffected():
    """The one handler that was always fine, kept as a contrast."""
    assert signals.setup_logging.has_listeners()
