"""Rosetta access control — restricts the translation editor to the
platform-global schema (plus any explicitly allowlisted schema) on top
of Rosetta's own staff/superuser gate.

``core.models.Translation`` is a SHARED (public-schema-only) model —
every Rosetta edit patches the ONE global gettext overlay that ships
to every tenant (see ``core/rosetta_storage.py``: ``apply_db_overlay``
mutates the process-wide catalog, and the version tick that propagates
it now lives under the schema-independent ``"global:"`` cache key —
see ``tenant/cache.py``). Without this check, django-tenants resolves
``/rosetta/...`` on ANY tenant host (Rosetta has no schema awareness
of its own), so a tenant-schema superuser — a tenant's own staff,
scoped to THAT tenant's Django admin — could reach the editor on their
own tenant domain and silently rewrite platform-wide copy served to
every other tenant.

``ROSETTA_ALLOWED_SCHEMAS`` (env, comma-separated, default ``"public"``)
lists the schemas Rosetta is reachable from. At cutover, operators set
it to ``"public,webside"`` so platform staff keep access via the
webside host during migration.
"""

from __future__ import annotations

from django.conf import settings
from django.db import connection

from rosetta.access import is_superuser_staff_or_in_translators_group


def _allowed_schemas() -> set[str]:
    raw = getattr(settings, "ROSETTA_ALLOWED_SCHEMAS", None)
    if not raw:
        from django_tenants.utils import get_public_schema_name  # noqa: PLC0415

        raw = get_public_schema_name()
    return {s.strip() for s in raw.split(",") if s.strip()}


def rosetta_schema_allowed() -> bool:
    return connection.schema_name in _allowed_schemas()


def tenant_scoped_rosetta_access(user) -> bool:
    """``ROSETTA_ACCESS_CONTROL_FUNCTION`` — schema allowlist AND the
    default rosetta staff/superuser/``translators``-group check."""
    return (
        rosetta_schema_allowed()
        and is_superuser_staff_or_in_translators_group(user)
    )
