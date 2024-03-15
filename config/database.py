from os import getenv

SYSTEM_ENV = getenv("SYSTEM_ENV", "dev")

CONN_HEALTH_CHECKS = True
ATOMIC_REQUESTS = True
CONN_MAX_AGE = 0
INDEX_MAXIMUM_EXPR_COUNT = 4000

DATABASES = {
    "default": {
        "ATOMIC_REQUESTS": True,
        "CONN_HEALTH_CHECKS": True,
        "TIME_ZONE": getenv("TIME_ZONE", "Europe/Athens"),
        "ENGINE": "django.db.backends.postgresql",
        "HOST": getenv("DB_HOST", "db"),
        "NAME": getenv("DB_NAME", "devdb"),
        "USER": getenv("DB_USER", "devuser"),
        "PASSWORD": getenv("DB_PASSWORD", "changeme"),
        "PORT": getenv("DB_PORT", "5432"),
    },
    "replica": {
        "TIME_ZONE": getenv("TIME_ZONE", "Europe/Athens"),
        "ENGINE": "django.db.backends.postgresql",
        "HOST": getenv("DB_HOST_TEST", "db_replica"),
        "NAME": getenv("DB_NAME_TEST", "devdb_replica"),
        "TEST": {
            "MIRROR": getenv("DB_TEST_MIRROR", "default"),
        },
    },
}

if SYSTEM_ENV == "ci":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "postgres",
            "USER": getenv("DB_USER", "devuser"),
            "PASSWORD": getenv("DB_PASSWORD", "changeme"),
            "HOST": "127.0.0.1",
            "PORT": "5432",
        }
    }

DBBACKUP_STORAGE = "django.core.files.storage.FileSystemStorage"
DBBACKUP_STORAGE_OPTIONS = {"location": "/backups"}
