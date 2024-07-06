import logging
import sys
import uuid
from datetime import datetime
from logging.config import dictConfig
from os import getenv
from os import makedirs
from os import path
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = getenv("DEBUG", "True") == "True"


def config_logging():
    unique_id = uuid.uuid4().hex
    timestamp = datetime.now().strftime("%d-%m-%Y")
    celery_log_filename = f"celery_logs_{timestamp}_{unique_id}.log"
    django_log_filename = f"django_logs_{timestamp}_{unique_id}.log"
    db_log_filename = f"db_logs_{timestamp}_{unique_id}.log"
    log_dir = path.join(BASE_DIR, "logs")
    makedirs(log_dir, exist_ok=True)  # Create the directory if it does not exist
    celery_log_file_path = path.join(log_dir, celery_log_filename)
    django_log_file_path = path.join(log_dir, django_log_filename)
    db_log_file_path = path.join(log_dir, db_log_filename)

    logging_level = getenv("LOGGING_LEVEL", "DEBUG" if DEBUG else "INFO")
    backup_count = int(getenv("LOG_BACKUP_COUNT", 30))

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
            "json": {
                "format": '{"timestamp": "%(asctime)s", "level": "%(levelname)s",'
                ' "module": "%(module)s", "message": "%(message)s"}',
                "datefmt": "%Y-%m-%dT%H:%M:%S",
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
                "backupCount": backup_count,  # how many backup file to keep, 30 days
                "formatter": "standard",
                "encoding": "utf-8",
            },
            "django_logfile": {
                "level": logging_level,
                "class": "logging.handlers.TimedRotatingFileHandler",
                "filename": django_log_file_path,
                "when": "D",
                "interval": 1,
                "backupCount": backup_count,
                "formatter": "standard",
                "encoding": "utf-8",
            },
            "db_logfile": {
                "level": "DEBUG",
                "class": "logging.handlers.TimedRotatingFileHandler",
                "filename": db_log_file_path,
                "when": "D",
                "interval": 1,
                "backupCount": backup_count,
                "formatter": "standard",
                "encoding": "utf-8",
            },
            "console": {
                "level": "INFO",
                "filters": ["require_debug_true"],
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": "verbose",
            },
            "mail_admins": {
                "level": "ERROR",
                "class": "django.utils.log.AdminEmailHandler",
                "filters": ["require_debug_false"],
                "formatter": "standard",
            },
            "json_logfile": {
                "level": logging_level,
                "class": "logging.handlers.TimedRotatingFileHandler",
                "filename": path.join(log_dir, f"json_logs_{timestamp}_{unique_id}.log"),
                "when": "D",
                "interval": 1,
                "backupCount": backup_count,
                "formatter": "json",
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "django": {
                "handlers": ["console", "django_logfile", "mail_admins", "json_logfile"],
                "propagate": True,
                "level": "DEBUG",
            },
            "django.db.backends": {
                "handlers": ["console", "db_logfile"],
                "level": "INFO",
                "propagate": False,
            },
            "celery": {
                "handlers": ["console", "celery_logfile"],
                "level": logging_level,
            },
            "django.request": {
                "handlers": ["django_logfile", "mail_admins"],
                "level": "ERROR",
                "propagate": False,
            },
        },
    }

    dictConfig(logging_config)
    return celery_log_filename, django_log_filename, logging_config


celery_filename, django_filename, LOGGING = config_logging()
celery_logger = logging.getLogger("celery")
django_logger = logging.getLogger("django")
