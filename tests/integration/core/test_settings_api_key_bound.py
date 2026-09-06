"""The settings endpoint must not be a window onto Django settings.

`Setting.get` is not a store-settings lookup. django-extra-settings
defaults `EXTRA_SETTINGS_FALLBACK_TO_CONF_SETTINGS` to True, so on a
miss it becomes `getattr(django.conf.settings, name)` — and the name
came straight off the query string.

That made `GET /api/v1/settings/get?key=SECRET_KEY` return the platform
secret key to any store-staff caller. With it, the holder can forge
session cookies and any `django.core.signing` value — password-reset
tokens included — for every user on every tenant. The same request read
`EMAIL_HOST_PASSWORD`, `DATABASES` (which carries the database
password), and the gateway's internal shared secret.

The gate is the declared registry: a key that is not a store setting is
now indistinguishable from one that does not exist.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.urls import reverse
from rest_framework.test import APIClient

from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

# Names that exist in `settings.py` and must never be reachable.
FORBIDDEN = [
    "SECRET_KEY",
    "EMAIL_HOST_PASSWORD",
    "DATABASES",
    "REDIS_URL",
    "CELERY_BROKER_URL",
]


def _staff_client():
    # A superuser passes `is_store_staff`, so this is the MOST
    # privileged caller the endpoint will ever serve — and it still must
    # not reach a Django setting.
    user = UserAccountFactory(is_staff=True, is_superuser=True)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.parametrize("key", FORBIDDEN)
def test_django_settings_are_not_reachable(key):
    assert hasattr(settings, key), (
        f"{key} must exist in settings for this test to prove anything"
    )
    response = _staff_client().get(reverse("api-settings-get"), {"key": key})
    assert response.status_code == 404, (
        f"?key={key} returned {response.status_code}; this endpoint "
        f"resolves store settings, never the Django settings module."
    )


def test_a_real_store_setting_still_resolves():
    """The gate must not break the endpoint's actual job."""
    key = settings.EXTRA_SETTINGS_DEFAULTS[0]["name"]
    response = _staff_client().get(reverse("api-settings-get"), {"key": key})
    assert response.status_code == 200
    assert response.data["name"] == key


def test_an_unknown_key_is_indistinguishable_from_a_refused_one():
    responses = [
        _staff_client()
        .get(reverse("api-settings-get"), {"key": key})
        .status_code
        for key in ("SECRET_KEY", "NO_SUCH_SETTING_AT_ALL")
    ]
    assert responses == [404, 404]
