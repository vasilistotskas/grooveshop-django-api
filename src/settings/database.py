"""PostgreSQL and the django-tenants routing."""

from os import getenv

from django.core.exceptions import ImproperlyConfigured

from settings.env import PRODUCTION_PROFILE, SYSTEM_ENV

# Use psycopg connection pool to bound DB connections per process. Each
# process maintains a pool of <DB_POOL_MAX_SIZE> connections regardless of
# the number of threads opened by the ASGI handler. Without this, asgiref's
# per-request ThreadPoolExecutor leaks Django thread-local connections that
# are only released when the worker thread is GC'd, which can exhaust
# Postgres max_connections under load.
DB_POOL_ENABLED = getenv("DB_POOL_ENABLED", "True") == "True"
DB_POOL_MIN_SIZE = int(getenv("DB_POOL_MIN_SIZE", "2"))
DB_POOL_MAX_SIZE = int(getenv("DB_POOL_MAX_SIZE", "8"))
DB_POOL_TIMEOUT = float(getenv("DB_POOL_TIMEOUT", "10"))

# How fast the SERVER finds out that a client is gone without having closed
# its socket (a worker's node lost, a network partition): TCP keepalives on
# each session, sent after DB_TCP_KEEPALIVES_IDLE idle seconds, every
# DB_TCP_KEEPALIVES_INTERVAL, given up after DB_TCP_KEEPALIVES_COUNT unanswered
# probes. Without them a session gets the server's own (on the cluster, a
# first probe after 900 s, then 9 more 100 s apart: about half an hour), and
# a session that holds a lock across a call to another service holds it
# that long: the delivery lock of an email (``core.db.locks``), and the store
# lock a running task takes (``tenant.celery.holding_store``). Django
# connects to PostgreSQL directly (no pooler), so the server's probes reach
# the worker's own socket. These GUCs are set per session, so they need no
# change to the server.
DB_TCP_KEEPALIVES_IDLE = int(getenv("DB_TCP_KEEPALIVES_IDLE", "30"))
DB_TCP_KEEPALIVES_INTERVAL = int(getenv("DB_TCP_KEEPALIVES_INTERVAL", "10"))
DB_TCP_KEEPALIVES_COUNT = int(getenv("DB_TCP_KEEPALIVES_COUNT", "3"))

_db_options: dict = {
    "connect_timeout": 5,
    "options": (
        "-c statement_timeout=30000 -c idle_in_transaction_session_timeout=10000"
        f" -c tcp_keepalives_idle={DB_TCP_KEEPALIVES_IDLE}"
        f" -c tcp_keepalives_interval={DB_TCP_KEEPALIVES_INTERVAL}"
        f" -c tcp_keepalives_count={DB_TCP_KEEPALIVES_COUNT}"
    ),
    # Enforce TLS to Postgres in production ('require' rejects an
    # unencrypted connection); dev/ci default to 'prefer' since local
    # Postgres usually has no server cert (G0362).
    "sslmode": getenv(
        "DB_SSLMODE",
        "require" if PRODUCTION_PROFILE else "prefer",
    ),
}
if DB_POOL_ENABLED:
    _db_options["pool"] = {
        "min_size": DB_POOL_MIN_SIZE,
        "max_size": DB_POOL_MAX_SIZE,
        "timeout": DB_POOL_TIMEOUT,
    }

DB_PASSWORD = getenv("DB_PASSWORD", "postgres")
if DB_PASSWORD == "postgres" and PRODUCTION_PROFILE:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "DB_PASSWORD must be set in production "
        "(current value is the insecure default 'postgres')."
    )
# ``require`` above is only the default: an explicit DB_SSLMODE would
# otherwise quietly downgrade the production profile to cleartext. These
# are the libpq modes that refuse an unencrypted connection.
_DB_TLS_SSLMODES = {"require", "verify-ca", "verify-full"}
if PRODUCTION_PROFILE and _db_options["sslmode"] not in _DB_TLS_SSLMODES:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        f"DB_SSLMODE={_db_options['sslmode']!r} allows an unencrypted "
        "database connection; production needs one of "
        f"{', '.join(sorted(_DB_TLS_SSLMODES))}."
    )

DATABASES = {
    "default": {
        "ATOMIC_REQUESTS": False,
        "AUTOCOMMIT": True,
        "CONN_HEALTH_CHECKS": True,
        # When OPTIONS["pool"] is set, the pool manages connection
        # lifetimes itself; Django requires CONN_MAX_AGE == 0 in this
        # mode (raises ImproperlyConfigured otherwise).
        "CONN_MAX_AGE": 0 if DB_POOL_ENABLED else 600,
        "TIME_ZONE": getenv("TIME_ZONE", "Europe/Athens"),
        "ENGINE": "django_tenants.postgresql_backend",
        "HOST": getenv("DB_HOST", "db"),
        "NAME": getenv("DB_NAME", "postgres"),
        "USER": getenv("DB_USER", "postgres"),
        "PASSWORD": DB_PASSWORD,
        "PORT": getenv("DB_PORT", "5432"),
        "OPTIONS": _db_options,
    },
}

DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"]
TENANT_MODEL = "tenant.Tenant"
TENANT_DOMAIN_MODEL = "tenant.TenantDomain"
# Checks each migration's deferred foreign keys when it ends, so a store
# provisioned inside one transaction migrates as a deploy does
# (``src/core/db/migrate_command.py``).
TENANT_BASE_MIGRATE_COMMAND = "core.db.migrate_command.Command"
PUBLIC_SCHEMA_URLCONF = "tenant.urls_public"
# django-tenants >= 3.14 issues ``SET search_path`` before EVERY cursor
# unless this flag caches it per connection (one extra round-trip per
# query otherwise). Safe since 4.0: the cache is invalidated on
# set_tenant/set_schema, on rollback() and close(), AND on a savepoint
# rollback (django-tenants#1263). PostgreSQL reverts the search_path on a
# savepoint rollback, so on 3.x a cached path could outlive it and the
# connection read another schema's rows. Keep the pin at >=4.0.
TENANT_LIMIT_SET_CALLS = True

if SYSTEM_ENV == "ci":
    DATABASES = {
        "default": {
            "ENGINE": "django_tenants.postgresql_backend",
            "NAME": getenv("DB_NAME", "postgres"),
            "USER": getenv("DB_USER", "postgres"),
            "PASSWORD": DB_PASSWORD,
            "HOST": "127.0.0.1",
            "PORT": "5432",
        }
    }
