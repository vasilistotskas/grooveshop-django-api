import os
import pytest
from django.conf import settings


@pytest.mark.django_db
def test_print_db_config():
    worker = os.environ.get("PYTEST_XDIST_WORKER", "unknown")
    db_name = settings.DATABASES["default"]["NAME"]
    test_db_name = (
        settings.DATABASES["default"].get("TEST", {}).get("NAME", "NOT_SET")
    )

    print(f"\n[WORKER: {worker}] Default Name: {db_name}")
    print(f"[WORKER: {worker}] Test Name: {test_db_name}")
    print(
        f"[WORKER: {worker}] DB Host: {settings.DATABASES['default'].get('HOST')}"
    )
    print(
        f"[WORKER: {worker}] DB Port: {settings.DATABASES['default'].get('PORT')}"
    )

    # Fail intentionally to see stdout
    assert False, f"Worker: {worker}, DB: {test_db_name}"
