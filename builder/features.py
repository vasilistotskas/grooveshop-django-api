"""Which store features are on, and where this person switches one on.

The editor marks a block whose feature is off (``feature_disabled``) and,
for someone who may, offers to turn the feature on. It must not copy the
plan/setting split or guess admin addresses, so Django answers both from
the tables the admin and the 404 gates already read
(``admin.features.StoreFeatures``): a feature is ``on`` when its plan flag
and every setting it needs are on, and ``turn_on_url`` is the admin page of
the first setting that is off, for a role that holds ``CONFIGURE_PERMISSION``
(ADMIN and OWNER). It is ``None`` when the feature is on, when the plan
keeps it off (only the platform can change that), or when the caller's role
cannot change settings.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.urls import reverse

from admin.features import (
    CONFIGURE_PERMISSION,
    Availability,
    StoreFeatures,
    store_features,
)
from core.utils.tenant_urls import get_tenant_api_base_url
from tenant.features import STORE_FEATURES


def setting_url(name: str) -> str:
    """The store admin's Store Settings page, searched for *name*, on the
    store's own admin host."""
    changelist = reverse("admin:extra_settings_setting_changelist")
    return f"{get_tenant_api_base_url()}{changelist}?{urlencode({'q': name})}"


def _turn_on_url(features: StoreFeatures, name: str) -> str | None:
    if features.state(name) is not Availability.SETTING_OFF:
        return None
    (first, *_rest) = features.settings_off(name)
    return setting_url(first)


def feature_states(request: Any) -> dict[str, dict[str, Any]]:
    """``{feature: {on, turn_on_url}}`` for every store feature, as the
    request's user sees it."""
    features = store_features(request)
    can_configure = request.user.has_perm(CONFIGURE_PERMISSION)
    return {
        name: {
            "on": features.live(name),
            "turn_on_url": _turn_on_url(features, name)
            if can_configure
            else None,
        }
        for name in STORE_FEATURES
    }
