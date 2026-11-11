"""Which store features are on, and where this person switches one on.

The editor marks a block whose feature is off (``feature_disabled``) and,
for someone who may, offers to turn the feature on. It must not copy the
plan/setting split or guess admin addresses, so Django answers both from
the tables the admin and the 404 gates already read
(``admin.features.StoreFeatures``): a feature is ``on`` when its plan flag
and every setting it needs are on, and ``turn_on_url`` is the admin change
page of the first setting that is off, for a role that holds
``CONFIGURE_PERMISSION`` (ADMIN and OWNER). It is ``None`` when the feature is on, when the plan
keeps it off (only the platform can change that), or when the caller's role
cannot change settings.
"""

from __future__ import annotations

from typing import Any

from django.urls import reverse
from extra_settings.models import Setting

from admin.features import (
    CONFIGURE_PERMISSION,
    Availability,
    store_features,
)
from core.utils.tenant_urls import get_tenant_api_base_url
from tenant.features import STORE_FEATURES


def setting_urls(names: set[str]) -> dict[str, str]:
    """The store admin's change page of each setting in *names*, on the
    store's own admin host, in one query. Every declared setting has its row
    (``migrate`` creates them, the settings admin refuses to delete one), so
    a name with no row is not declared and has no page."""
    base = get_tenant_api_base_url()
    return {
        name: base + reverse("admin:extra_settings_setting_change", args=[pk])
        for name, pk in Setting.objects.filter(name__in=names).values_list(
            "name", "pk"
        )
    }


def feature_states(request: Any) -> dict[str, dict[str, Any]]:
    """``{feature: {on, turn_on_url}}`` for every store feature, as the
    request's user sees it."""
    features = store_features(request)
    first_off: dict[str, str] = {}
    if request.user.has_perm(CONFIGURE_PERMISSION):
        first_off = {
            name: features.settings_off(name)[0]
            for name in STORE_FEATURES
            if features.state(name) is Availability.SETTING_OFF
        }
    urls = setting_urls(set(first_off.values())) if first_off else {}
    return {
        name: {
            "on": features.live(name),
            "turn_on_url": urls.get(first_off.get(name, "")),
        }
        for name in STORE_FEATURES
    }
