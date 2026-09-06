"""An admin action with no declared permission is offered to everyone.

Django and unfold both FAIL OPEN on one. Django's
`_filter_actions_by_permissions` keeps an action that declares nothing,
and unfold's `_filter_unfold_actions_by_permissions` appends it
unconditionally:

    if not hasattr(action.method, "allowed_permissions"):
        filtered_actions.append(action)
        continue

So the action reaches anyone who passes the admin site's own gate.
`actions_detail` is worse: unfold registers those as real URLs wrapped
only in `admin_site.admin_view` — active and `is_staff` — so a member
with no model permissions at all could GET one directly and cancel a
parcel or purge a payout.

Eighty actions across this codebase declared nothing. The mechanism was
available and simply unused, which is why `BaseModelAdmin` now supplies
a default rather than each admin being annotated by hand.
"""

from __future__ import annotations

import pytest
from django.contrib import admin as django_admin

from admin.base import BaseModelAdmin


def _repo_actions():
    for model_admin in django_admin.site._registry.values():
        if not isinstance(model_admin, BaseModelAdmin):
            continue
        names = set()
        for attribute in BaseModelAdmin._ACTION_ATTRIBUTES:
            names.update(
                name
                for name in (getattr(model_admin, attribute, None) or [])
                if isinstance(name, str)
            )
        for name in sorted(names):
            method = getattr(type(model_admin), name, None)
            if method is not None:
                yield f"{type(model_admin).__name__}.{name}", method


def test_every_repo_admin_action_declares_a_permission():
    undeclared = [
        label
        for label, method in _repo_actions()
        if not getattr(method, "allowed_permissions", None)
    ]

    assert not undeclared, (
        "These admin actions declare no permission, so Django and unfold "
        "both offer them to any staff member:\n  " + "\n  ".join(undeclared)
    )


def test_the_detector_sees_actions_at_all():
    """A guard that matched nothing would pass with the bug wide open."""
    assert sum(1 for _ in _repo_actions()) > 50


@pytest.mark.parametrize(
    "label",
    [
        "BoxNowShipmentAdmin.cancel_parcels",
        "AcsShipmentAdmin.issue_voucher_now",
        "BlogPostAdmin.publish_posts",
    ],
)
def test_the_destructive_ones_specifically(label):
    """Named so a regression on these reads as itself, not as a count."""
    found = dict(_repo_actions())

    assert label in found, f"{label} is no longer registered"
    assert found[label].allowed_permissions
