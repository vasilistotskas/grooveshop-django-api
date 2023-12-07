import logging
import sys
from datetime import datetime
from logging.config import dictConfig
from os import getenv
from os import makedirs
from os import path
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = getenv("DEBUG", "True") == "True"


def config_logging():
    timestamp = datetime.now().strftime("%d-%m-%Y")
    celery_log_filename = f"celery_logs_{timestamp}.log"
    django_log_filename = f"django_logs_{timestamp}.log"
    log_dir = path.join(BASE_DIR, "logs")
    makedirs(log_dir, exist_ok=True)  # Create the directory if it does not exist
    celery_log_file_path = path.join(log_dir, celery_log_filename)
    django_log_file_path = path.join(log_dir, django_log_filename)
    logging_level = "DEBUG" if DEBUG else "INFO"
    logging_config = {
        "version": 1,
        "disable_existing_loggers": True,
        "formatters": {
            "standard": {
                "format": "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s",
                "datefmt": "%d/%b/%Y %H:%M:%S",
            },
            "verbose": {
                "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
                "style": "{",
            },
            "simple": {
                "format": "[%(asctime)s] %(levelname)s | %(funcName)s | %(name)s | %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "filters": {
            "require_debug_true": {
                "()": "django.utils.log.RequireDebugTrue",
            },
            "require_debug_false": {
                "()": "django.utils.log.RequireDebugFalse",
            },
        },
        "handlers": {
            "null": {
                "level": logging_level,
                "class": "logging.NullHandler",
            },
            "celery_logfile": {
                "level": logging_level,
                "class": "logging.handlers.TimedRotatingFileHandler",
                "filename": celery_log_file_path,
                "when": "D",  # this specifies the interval
                "interval": 1,  # defaults to 1, only necessary for other values
                "backupCount": 30,  # how many backup file to keep, 10 days
                "formatter": "standard",
            },
            "django_logfile": {
                "level": logging_level,
                "class": "logging.handlers.TimedRotatingFileHandler",
                "filename": django_log_file_path,
                "when": "D",
                "interval": 1,
                "backupCount": 30,
                "formatter": "standard",
            },
            "console": {
                "level": "INFO",
                "filters": ["require_debug_true"],
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": "verbose",
            },
        },
        "loggers": {
            "django": {
                "handlers": ["console", "django_logfile"],
                "propagate": True,
                "level": "WARNING",
            },
            "django.db.backends": {
                "handlers": ["console"],
                "level": "INFO",
                "propagate": False,
            },
            "celery": {
                "handlers": ["console", "celery_logfile"],
                "level": logging_level,
            },
        },
    }
    dictConfig(logging_config)
    return celery_log_filename, django_log_filename, logging_config


celery_filename, django_filename, LOGGING = config_logging()
celery_logger = logging.getLogger("celery")
django_logger = logging.getLogger("django")
