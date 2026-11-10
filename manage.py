#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys
from pathlib import Path

# The apps, ``settings`` and ``asgi`` live in ``src/`` and are imported by
# their bare names, so that directory has to be on the Python path. The
# images also set ``PYTHONPATH`` (for ``celery -A core`` and gunicorn,
# which never run this file); this line is what a bare
# ``python manage.py ...`` on a checkout relies on.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
