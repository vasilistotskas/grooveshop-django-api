"""Celery tasks the email tests run, registered once.

A task defined inside a fixture or a test is a new callable on every call,
and every Celery app finalized later in the run (the beat and schedule tests
build their own) registers each of them under the one name and warns
``DuplicateTaskNameWarning``. Defined here, each name has one callable.

Each sends the probe types of ``tests.utils.emails``, which the test
registers with its ``catalogue`` fixture first.
"""

from __future__ import annotations

from core import celery_app
from emails.recipients import Recipient
from emails.send import send_email
from tests.utils.emails import probe_object


@celery_app.task(bind=True, max_retries=2, name="tests.emails.send_retrying")
def send_retrying(self):
    """Sends ``probe.notice`` and retries on a transport failure. Run with
    ``apply()``, it keeps the task id over ``retry()``, as a worker does."""
    try:
        send_email("probe.notice", probe_object())
    except OSError as error:
        raise self.retry(exc=error, countdown=0) from error


@celery_app.task(bind=True, name="tests.emails.send_three_deliveries")
def send_three_deliveries(self):
    """One task, three deliveries: another recipient, another type."""
    send_email("probe.notice", probe_object())
    send_email(
        "probe.notice",
        probe_object(),
        recipient=Recipient("other@example.test", "en"),
    )
    send_email("probe.other", probe_object())


@celery_app.task(bind=True, name="tests.emails.send_once")
def send_once(self):
    """Sends ``probe.notice`` once."""
    send_email("probe.notice", probe_object())
