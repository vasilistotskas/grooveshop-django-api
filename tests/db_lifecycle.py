"""Which test databases outlive their pytest session.

``addopts`` passes ``--reuse-db`` so the developer's own runs skip the
migration replay. A run that overrides ``DB_NAME`` (the documented way to
run beside another session) builds a fresh set of databases under a name
nobody reuses, and ``--reuse-db`` used to keep that set too: 854 test
databases had piled up on the shared local Postgres by 2026-11-02. Every
one of their tables and indexes holds an entry in the server's cumulative
statistics, which live in dynamic shared memory (``/dev/shm``), so the
pile-up is also what exhausted ``/dev/shm`` and crashed the server.

Only the configured database name keeps its test databases: the
``DB_NAME`` in ``.env`` when that file sets one, otherwise the settings
default (no ``DB_NAME`` in the environment). A run under any other name
drops its databases when the session ends. A run that is killed before
then still leaves its set behind; ``docs/testing.md`` has the sweep.
"""

from os import environ

import pytest
from django.conf import settings
from dotenv import dotenv_values


def _db_name_is_configured_default(name: str) -> bool:
    env_file = settings.BASE_DIR / ".env"
    configured = (
        dotenv_values(env_file).get("DB_NAME") if env_file.is_file() else None
    )
    if configured is not None:
        return name == configured
    return "DB_NAME" not in environ


@pytest.fixture(scope="session")
def django_db_keepdb(request: pytest.FixtureRequest) -> bool:
    """pytest-django's fixture, narrowed to the configured database name."""
    return bool(request.config.getvalue("reuse_db")) and (
        _db_name_is_configured_default(settings.DATABASES["default"]["NAME"])
    )
