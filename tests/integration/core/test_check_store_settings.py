"""``manage.py check_store_settings`` stops a deploy that would serve a
saved setting its declared validator refuses."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from extra_settings.models import Setting

pytestmark = pytest.mark.django_db


def _store(name: str, value: object) -> None:
    Setting.objects.update_or_create(
        name=name,
        defaults={"value_type": Setting.TYPE_JSON, "value_json": value},
    )


def test_valid_settings_pass():
    _store("ANNOUNCEMENT_BAR", {"enabled": False, "text": ""})

    out = StringIO()
    call_command("check_store_settings", stdout=out)

    assert "passes its validator" in out.getvalue()


def test_a_refused_value_fails_the_deploy_naming_the_store_and_setting():
    _store("ANNOUNCEMENT_BAR", {"enabled": "yes"})
    err = StringIO()

    with pytest.raises(CommandError):
        call_command("check_store_settings", stderr=err)

    assert ": ANNOUNCEMENT_BAR fails its validator" in err.getvalue()
    # never the value
    assert "yes" not in err.getvalue()
