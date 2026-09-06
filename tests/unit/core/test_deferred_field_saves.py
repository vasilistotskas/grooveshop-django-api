"""Deferred-field fetches must not be saved on history-tracked money models.

``django-money``'s ``MoneyFieldProxy.__get__`` reads
``obj.__dict__[field.name]`` directly instead of going through Django's
``DeferredAttribute``, so it never lazy-loads a deferred column — it
raises ``KeyError``.  On its own that is harmless, because nothing reads
the amount.  ``simple_history``'s ``post_save`` hook, however, snapshots
*every* field (``create_historical_record``: ``getattr(instance,
field.attname)`` for each), regardless of ``update_fields``.  Put the two
on the same model and any ``save()`` of a row fetched with the money
column deferred dies with ``KeyError: '<field>'`` from inside a signal
receiver.

Verified against django-money 3.6.1, which is the current release — this
is not a version we can upgrade past.

The failure is nastier than a plain crash because the three sites in this
codebase that hit it all sat inside a best-effort ``except Exception:
logger.…`` — so the save silently did not happen and the code carried on
reporting the cleanup it had not performed.  It has been reintroduced
after being fixed and documented in prose twice in the same module, which
is why this is a test and not a comment.

The rule is deliberately narrow: only the models that carry BOTH a
``MoneyField`` and ``HistoricalRecords``, and only when the same function
both defers and saves.  Deferring for a read-only lookup is fine and
common (``order/admin.py`` does it to resolve an email).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from django.apps import apps
from djmoney.models.fields import MoneyField
from simple_history.models import registered_models

REPO_ROOT = Path(__file__).resolve().parents[3]

_EXCLUDED_DIRS = frozenset(
    {".venv", "tests", "tests_mt", "migrations", "node_modules", ".git"}
)
_DEFERRING_CALLS = frozenset({"only", "defer"})


def _affected_model_names() -> frozenset[str]:
    """Models whose ``save()`` snapshots fields a deferred fetch omits.

    ``registered_models`` is simple-history's own record of what it
    tracks, keyed by db_table.  Reading it beats introspecting the class
    for a ``HistoricalRecords`` attribute, which is not there: the
    descriptor replaces itself with a ``HistoryDescriptor`` during
    ``contribute_to_class``.  (The first version of this test looked for
    the former, matched nothing, and passed against the very bug it was
    written to catch.)
    """
    tracked = {model._meta.db_table for model in registered_models.values()}
    return frozenset(
        model.__name__
        for model in apps.get_models()
        if model._meta.db_table in tracked
        and any(isinstance(f, MoneyField) for f in model._meta.get_fields())
    )


def _source_files() -> list[Path]:
    return [
        path
        for path in REPO_ROOT.rglob("*.py")
        if not _EXCLUDED_DIRS & set(path.relative_to(REPO_ROOT).parts)
    ]


def _root_name(node: ast.AST) -> str | None:
    """Walk an attribute/call chain back to its leftmost ``Name``."""
    while True:
        match node:
            case ast.Call(func=inner) | ast.Attribute(value=inner):
                node = inner
            case ast.Name(id=name):
                return name
            case _:
                return None


def _offending_functions(
    tree: ast.AST, affected: frozenset[str]
) -> list[tuple[str, int, str]]:
    findings = []
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        deferred: list[tuple[int, str]] = []
        saves = False
        for node in ast.walk(func):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if node.func.attr == "save":
                saves = True
            elif node.func.attr in _DEFERRING_CALLS:
                model = _root_name(node.func.value)
                if model in affected:
                    deferred.append((node.lineno, model))
        if saves:
            findings.extend(
                (func.name, lineno, model) for lineno, model in deferred
            )
    return findings


@pytest.mark.django_db
def test_no_deferred_fetch_is_saved_on_a_history_tracked_money_model():
    affected = _affected_model_names()
    assert affected, (
        "Found no model with both a MoneyField and HistoricalRecords — "
        "the detector is broken, not the codebase."
    )

    offenders = []
    for path in _source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError, UnicodeDecodeError:  # pragma: no cover
            continue
        for func_name, lineno, model in _offending_functions(tree, affected):
            rel = path.relative_to(REPO_ROOT).as_posix()
            offenders.append(f"{rel}:{lineno} {func_name}() defers {model}")

    assert not offenders, (
        "A deferred-field fetch is saved in the same function. "
        "simple_history snapshots every field on save and django-money "
        "will not lazy-load the deferred amount, so this raises "
        "KeyError: '<money field>' from inside the post_save receiver:\n  "
        + "\n  ".join(sorted(offenders))
    )
