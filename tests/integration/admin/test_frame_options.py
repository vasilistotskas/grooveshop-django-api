"""Who may frame what: DENY everywhere, SAMEORIGIN on the admin pages
Unfold's related-object modals load.

django-unfold 0.107 replaced the admin's related-object popups with
modals that load ``?_popup=1`` in an iframe on the same origin, and
marks ``changelist_view``/``changeform_view`` SAMEORIGIN for that. In
production Traefik's ``security-headers-backend`` middleware no longer
sets a frame header on admin-serving routers (grooveshop-infrastructure
``base/ingress-traefik.yaml``), so these Django headers are what the
browser receives.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client_():
    client = Client()
    client.force_login(
        UserAccountFactory(admin=True),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )
    return client


def test_the_modal_targets_may_be_framed_by_the_same_origin(admin_client_):
    for url in (
        reverse("admin:vat_vat_add") + "?_popup=1",
        reverse("admin:vat_vat_changelist") + "?_popup=1",
    ):
        response = admin_client_.get(url)
        assert response.status_code == 200, url
        assert response["X-Frame-Options"] == "SAMEORIGIN", url


def test_other_admin_pages_may_not_be_framed(admin_client_):
    response = admin_client_.get(reverse("admin:index"))
    assert response.status_code == 200
    assert response["X-Frame-Options"] == "DENY"


def test_the_api_may_not_be_framed():
    response = Client().get(reverse("country-list"))
    assert response["X-Frame-Options"] == "DENY"
