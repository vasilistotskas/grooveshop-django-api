"""A Celery task's name is an address, and addresses are locked.

A task with no ``name=`` is named after the module it lives in
(``order.tasks.send_x``). Three things hold that string outside the
code: a message already waiting in RabbitMQ, an entry of
``CELERY_BEAT_SCHEDULE``, and a ``tenant.DeferredDispatch`` row kept
for a later publish. A deploy runs the old and the new release side by
side, so a task that changes module without keeping its name is
"unregistered" to whichever release did not send it, and its work is
dropped without an error anyone sees.

``celery_task_names.txt`` is the list of every first-party task name.
Moving a task keeps the list unchanged (give it ``name="<the name it
has today>"``). Adding a task adds a line. Removing or renaming one is
an edit of that file by hand, and that edit is the moment to check the
queue, the beat schedule and the deferred rows for the old name.
"""

from __future__ import annotations

import sys
from pathlib import Path

from core import celery_app

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_FILE = Path(__file__).with_name("celery_task_names.txt")
# Tests define throwaway tasks on the same app; in a full run they are
# registered by the time this file runs.
_NOT_THE_PROJECT = frozenset({".venv", "site-packages", "tests", "tests_mt"})


def _is_first_party(task) -> bool:
    module = sys.modules.get(type(task).__module__)
    source = getattr(module, "__file__", None)
    if source is None:
        return False
    path = Path(source).resolve()
    return path.is_relative_to(REPO_ROOT) and not _NOT_THE_PROJECT & set(
        path.relative_to(REPO_ROOT).parts
    )


def _registered_names() -> set[str]:
    celery_app.autodiscover_tasks(force=True)
    return {
        name for name, task in celery_app.tasks.items() if _is_first_party(task)
    }


def _locked_names() -> set[str]:
    return set(LOCK_FILE.read_text(encoding="utf-8").splitlines())


def test_the_walk_sees_the_projects_tasks():
    assert len(_registered_names()) >= 150


def test_no_task_name_disappeared():
    missing = sorted(_locked_names() - _registered_names())
    assert not missing, (
        "These task names are no longer registered. If the task moved, "
        'give it name="<old name>" so queued messages, the beat schedule '
        "and DeferredDispatch rows still reach it. If it was removed on "
        "purpose, check those three for the old name, then delete its "
        f"line from {LOCK_FILE.name}:\n  " + "\n  ".join(missing)
    )


def test_every_task_name_is_in_the_lock_file():
    unlisted = sorted(_registered_names() - _locked_names())
    assert not unlisted, (
        f"Add these task names to {LOCK_FILE.name} (one per line, "
        "sorted):\n  " + "\n  ".join(unlisted)
    )


def test_the_lock_file_is_sorted_and_unique():
    lines = LOCK_FILE.read_text(encoding="utf-8").splitlines()
    assert lines == sorted(set(lines))
