"""The log configuration (``LOGGING``) for each kind of host."""

from os import getenv, makedirs, path

from settings.env import (
    BASE_DIR,
    IS_DEVELOPMENT,
    IS_DOCKER,
    IS_KUBERNETES,
    PRODUCTION_PROFILE,
)

# Logging: Kubernetes writes one stream for the log collector (JSON under
# the production profile); the compose stack and a developer's machine
# write a short console line and a rotating file; anything else (CI) a
# console line only.
logging_level = getenv("LOGGING_LEVEL", "INFO")

_log_line_format = (
    "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] "
    "[cid=%(correlation_id)s] [schema=%(schema_name)s] "
    "%(funcName)s: %(message)s"
)
_log_filter_classes = {
    "add_hostname": "core.logging.HostnameFilter",
    "add_correlation_id": "core.logging.CorrelationIdFilter",
    "add_tenant_context": "core.logging.TenantContextFilter",
    # Silences the CancelledError tracebacks asyncio emits when an ASGI
    # client disconnects mid-request.
    "drop_asyncio_cancelled": "core.logging.DropAsyncioCancelledError",
    # Flower's clean shutdown.
    "drop_system_exit": "core.logging.DropSystemExit",
}
# ``django`` is declared in every environment so dictConfig drops the
# handlers Django attaches by default (``mail_admins``): a server error
# is a log line, not an email.
_service_loggers = {
    "django": {
        "level": "WARNING",
        "propagate": True,
    },
    "celery": {
        "level": logging_level,
        "propagate": True,
    },
    "asyncio": {
        "level": logging_level,
        "filters": ["drop_asyncio_cancelled"],
        "propagate": True,
    },
    "tornado.application": {
        "level": logging_level,
        "filters": ["drop_system_exit"],
        "propagate": True,
    },
}

if IS_KUBERNETES:
    _log_formatters = {
        # A real serialiser, not a format string that looks like JSON.
        # The string version could not escape the message and appended
        # tracebacks outside the object — see JsonFormatter.
        "json": {
            "()": "core.logging.JsonFormatter",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        },
        "console": {
            "format": _log_line_format,
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    }
    _log_filters = (
        "add_hostname",
        "add_correlation_id",
        "add_tenant_context",
        "drop_asyncio_cancelled",
        "drop_system_exit",
    )
    _log_handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if PRODUCTION_PROFILE else "console",
            "level": logging_level,
            "filters": [
                "add_hostname",
                "add_correlation_id",
                "add_tenant_context",
            ],
        },
    }
    _log_root_level = logging_level
    _log_loggers = _service_loggers

elif IS_DOCKER or IS_DEVELOPMENT:
    log_dir = path.join(BASE_DIR, "logs")
    makedirs(log_dir, exist_ok=True)

    container_id = getenv("HOSTNAME", "dev")[:12]
    app_log_file = path.join(log_dir, f"grooveshop_{container_id}.log")

    backup_count = int(getenv("LOG_BACKUP_COUNT", "30"))

    _log_formatters = {
        "verbose": {
            "format": _log_line_format,
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "simple": {
            "format": "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            "datefmt": "%H:%M:%S",
        },
    }
    _log_filters = (
        "add_correlation_id",
        "add_tenant_context",
        "drop_asyncio_cancelled",
        "drop_system_exit",
    )
    _log_handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "level": "INFO",
        },
        "app_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": app_log_file,
            # Not the locale's encoding: on Windows that is cp1252,
            # which cannot write a Greek log line.
            "encoding": "utf-8",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": backup_count,
            "level": logging_level,
            "formatter": "verbose",
            "filters": ["add_correlation_id", "add_tenant_context"],
        },
    }
    _log_root_level = logging_level
    _log_loggers = _service_loggers

else:
    _log_formatters = {
        "simple": {
            "format": "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    }
    _log_filters = ("add_tenant_context",)
    _log_handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "filters": ["add_tenant_context"],
        },
    }
    _log_root_level = "INFO"
    # INFO is Django's own default level for this logger.
    _log_loggers = {
        "django": {
            "level": "INFO",
            "propagate": True,
        },
    }

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": _log_formatters,
    "filters": {
        name: {"()": _log_filter_classes[name]} for name in _log_filters
    },
    "handlers": _log_handlers,
    "root": {
        "handlers": list(_log_handlers),
        "level": _log_root_level,
    },
    "loggers": _log_loggers,
}
