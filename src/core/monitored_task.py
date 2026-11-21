"""``MonitoredTask``, the base of every first-party Celery task, and the
masking of task arguments in its log lines.
"""

import logging
import time
from typing import Any

from core import db_cost
from core.retry import is_permanent_failure
from tenant.celery import TenantTask

logger = logging.getLogger(__name__)


_SECRET_KEYS = frozenset(
    {"password", "token", "key", "secret", "auth", "credential", "passwd"}
)
_ARG_TRUNCATE = 500


def _safe_repr(value: Any, key: str | None = None) -> str:
    """Return a truncated repr of *value*, masking secret-looking keys."""
    if key is not None:
        lower = str(key).lower()
        if any(sk in lower for sk in _SECRET_KEYS):
            return "***"
    text = repr(value)
    if len(text) > _ARG_TRUNCATE:
        return text[:_ARG_TRUNCATE] + "…"
    return text


def safe_task_arguments(
    task: Any, args: Any, kwargs: Any
) -> tuple[list[str], dict[str, str]]:
    """A task's arguments as a log line may show them.

    Masks what ``_safe_repr`` catches by name and what the task declares in
    ``sensitive_kwargs``. ``task`` is ``None`` when nothing in this process
    knows it: nothing then says which of its values is secret, so none is
    shown.
    """
    if task is None:
        return ["***"] * len(args or []), dict.fromkeys(kwargs or {}, "***")
    sensitive = getattr(task, "sensitive_kwargs", frozenset())
    return (
        [_safe_repr(a) for a in (args or [])],
        {
            k: ("***" if k in sensitive else _safe_repr(v, key=k))
            for k, v in (kwargs or {}).items()
        },
    )


class MonitoredTask(TenantTask):
    """Base task with tenant context propagation and monitoring."""

    #: Kwarg names whose VALUES must never reach the logs, whatever they
    #: are called. ``_safe_repr`` masks by NAME — it catches ``token`` and
    #: ``password`` — but a payload can carry a secret under an innocent
    #: key. ``send_allauth_email_task`` is the case: its ``context`` kwarg
    #: carries the password-reset link or the login code, which would
    #: otherwise land in an ERROR log on the exact failure this masking
    #: exists for.
    sensitive_kwargs: frozenset[str] = frozenset()

    def retry(self, *args: Any, **options: Any) -> Any:
        """Reschedule the task, unless ``exc`` says a retry cannot help.

        Both ``autoretry_for`` and an explicit ``raise self.retry(...)``
        land here, so an SMTP 5xx or an HTTP 4xx stops at once however
        the task asked for the retry. See ``core.retry``. Celery passes
        ``exc`` by keyword from every call site.
        """
        exc = options.get("exc")
        if exc is not None and is_permanent_failure(exc):
            raise exc
        return super().retry(*args, **options)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Run the task and keep what it cost the database on its request.

        ``on_success`` and ``on_failure`` run after ``__call__`` returns,
        on the same request context, so the figures are stored there.
        """
        started = time.perf_counter()
        with db_cost.measure() as cost:
            try:
                return super().__call__(*args, **kwargs)
            finally:
                self.request.cost_fields = {
                    "duration_ms": round(
                        (time.perf_counter() - started) * 1000, 1
                    ),
                    **cost.as_fields(),
                }

    def _cost_fields(self) -> dict[str, Any]:
        return getattr(self.request, "cost_fields", {})

    def on_success(self, retval, task_id, args, kwargs):
        logger.info(
            f"Task {self.name} completed successfully. Task ID: {task_id}",
            extra={"fields": self._cost_fields()},
        )

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        safe_args, safe_kwargs = safe_task_arguments(self, args, kwargs)
        # ``exc_info=exc`` and not ``einfo.exception``: Celery wraps the
        # latter in billiard's ``ExceptionWithTraceback`` (its ``str()``
        # is the traceback text and its ``__traceback__`` is ``None``),
        # so passing it would log a bogus exception type with no frames.
        # ``exc`` is the exception the task raised, with its traceback
        # attached by Celery before this handler runs.
        logger.error(
            "Task %s failed. Task ID: %s, Error: %s, args=%s, kwargs=%s",
            self.name,
            task_id,
            exc,
            safe_args,
            safe_kwargs,
            exc_info=exc,
            extra={"fields": self._cost_fields()},
        )
