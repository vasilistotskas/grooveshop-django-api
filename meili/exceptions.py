"""Exception classes for the Meilisearch integration.

Every failure here used to be a bare ``raise Exception(...)``, which
callers could only catch as `Exception` — indistinguishable from a bug in
the calling code. `meili/apps.py` did exactly that: it raised
``Exception`` and caught ``Exception`` a line later, so a genuine
TypeError in the surrounding block would have been swallowed as an
indexing failure.

Exception Hierarchy:
    MeiliError (base)
    ├── MeiliTaskFailed      — an async Meilisearch task ended in `failed`
    └── MeiliSettingsError   — applying index settings did not take
"""

from __future__ import annotations


class MeiliError(Exception):
    """Base class for every Meilisearch integration failure."""


class MeiliTaskFailed(MeiliError):
    """A Meilisearch task finished with status ``failed``.

    Meilisearch accepts writes asynchronously and reports the outcome on
    the task, not the request — so a 202 says only that the work was
    queued. ``task.error`` carries the reason it later failed.
    """

    def __init__(self, error: object, *, operation: str | None = None):
        self.error = error
        self.operation = operation
        detail = f"{operation}: {error}" if operation else str(error)
        super().__init__(f"Meilisearch task failed — {detail}")


class MeiliSettingsError(MeiliError):
    """Applying an index's settings (ranking, synonyms, typo tolerance)
    did not take effect."""
