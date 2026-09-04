"""Nothing outside the public-schema authentication layer may read
``user.is_staff``.

On an API request ``request.user`` is a row from the TENANT schema, where
``is_staff`` is customer residue from the id-preserving cutover. The only
sound "store staff" question is ``tenant.membership.is_store_staff``. This
test walks the AST of every application module so a new ``is_staff``
branch fails CI instead of quietly re-opening the hole.
"""

from __future__ import annotations

import ast
from pathlib import Path

from django.conf import settings

# Modules that legitimately read the flag: they authenticate against the
# PUBLIC schema (admin login, staff tokens), render admin chrome, or
# manage the flag itself.
ALLOWED = {
    "admin",
    "tenant/auth_backends.py",
    "tenant/api_tokens.py",
    "tenant/staff_api.py",
    "user/admin.py",
    "user/managers/account.py",
    "core/rosetta_access.py",
    "core/context_processors.py",
    "devtools",
}
SKIP_DIRS = {"tests", "tests_mt", "migrations", ".venv", "node_modules"}


def _is_allowed(rel: str) -> bool:
    return any(rel == entry or rel.startswith(entry + "/") for entry in ALLOWED)


def _app_modules():
    root = Path(settings.BASE_DIR)
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if rel in {"settings.py", "manage.py"}:
            continue
        yield rel, path


def _is_staff_reads(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "is_staff"
            or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "is_staff"
            )
        ):
            yield node.lineno


def test_is_staff_is_only_read_by_the_public_schema_auth_layer():
    offenders = []
    for rel, path in _app_modules():
        if _is_allowed(rel):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        offenders.extend(f"{rel}:{line}" for line in _is_staff_reads(tree))
    assert not offenders, (
        "is_staff read outside the public-schema auth layer — use "
        f"tenant.membership.is_store_staff instead: {offenders}"
    )
