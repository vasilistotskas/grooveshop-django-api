"""Who may edit the storefront's wording: ADMIN and OWNER.

STAFF see the editor read-only. The rule is the role system's
(``tenant.role_scopes``), so these tests go through real memberships and
the real admin, not through a patched permission.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import translation

from tenant.models import TenantMembershipRole
from tests.integration.texts.conftest import staff_client
from tests.utils.staff import store_staff
from tests.utils.texts import FOOTER
from texts import services
from texts.factories import TextOverrideFactory
from texts.models import TextOverride

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
TAGLINE = {"namespace": FOOTER, "key": "tagline"}


@pytest.fixture(autouse=True)
def _english():
    with translation.override("en"):
        yield


def url(name: str = "changelist") -> str:
    return reverse(f"admin:texts_textoverride_{name}")


@pytest.fixture
def staff(tenant):
    return staff_client(tenant, TenantMembershipRole.STAFF)


class TestThePermissions:
    @pytest.mark.parametrize(
        "role", [TenantMembershipRole.ADMIN, TenantMembershipRole.OWNER]
    )
    def test_admin_and_owner_may_change_texts(self, tenant, role):
        user = store_staff(tenant, role=role)

        assert user.has_perm("texts.change_textoverride")
        assert user.has_perm("texts.view_textoverride")

    def test_staff_may_only_view_them(self, tenant):
        user = store_staff(tenant, role=TenantMembershipRole.STAFF)

        assert user.has_perm("texts.view_textoverride")
        for action in ("add", "change", "delete"):
            assert not user.has_perm(f"texts.{action}_textoverride"), action

    def test_staff_keep_the_rest_of_the_operational_admin(self, tenant):
        user = store_staff(tenant, role=TenantMembershipRole.STAFF)

        assert user.has_perm("order.change_order")


class TestStaffCannotWrite:
    def test_save_reset_and_restore_are_refused(self, stub, tenant):
        user = store_staff(tenant, role=TenantMembershipRole.STAFF)
        row = TextOverrideFactory(
            namespace=FOOTER, key="tagline", language="en", value="Ours"
        )

        with pytest.raises(PermissionDenied):
            services.save_texts(user, **TAGLINE, values={"en": "New"})
        with pytest.raises(PermissionDenied):
            services.reset_text(user, **TAGLINE)
        with pytest.raises(PermissionDenied):
            services.restore_text(user, history_id=1)

        assert TextOverride.objects.get(pk=row.pk).value == "Ours"
        assert stub.posts == []

    def test_the_endpoints_answer_403(self, stub, staff):
        client, _user = staff
        TextOverrideFactory(
            namespace=FOOTER, key="tagline", language="en", value="Ours"
        )
        history = TextOverride.history.first()
        calls = {
            "validate": {**TAGLINE, "language": "en", "value_en": "Hi"},
            "save": {**TAGLINE, "value_en": "New"},
            "reset": TAGLINE,
            "restore": {**TAGLINE, "history_id": history.history_id},
        }

        for name, data in calls.items():
            response = client.post(url(name), data, **HTMX)
            assert response.status_code == 403, name

        assert TextOverride.objects.get(key="tagline").value == "Ours"
        assert stub.posts == []

    def test_the_editor_opens_read_only_with_a_notice(self, stub, staff):
        client, _user = staff

        response = client.get(url(), {"q": "tagline"})

        html = response.content.decode()
        assert response.status_code == 200
        assert "do not have permission to change them" in html
        article = html.split("<article")[1].split("</article>")[0]
        assert "disabled" in article
        assert ">save</span>" not in article

    def test_staff_may_read_the_history(self, stub, staff):
        client, _user = staff

        response = client.get(url("history"), dict(TAGLINE), **HTMX)

        assert response.status_code == 200

    def test_the_sidebar_still_lists_the_editor_for_staff(self, stub, staff):
        client, _user = staff

        assert url() in client.get(url()).content.decode()


@pytest.mark.parametrize(
    "role", [TenantMembershipRole.ADMIN, TenantMembershipRole.OWNER]
)
def test_admin_and_owner_can_edit_through_the_editor(stub, tenant, role):
    client, _user = staff_client(tenant, role)

    page = client.get(url(), {"q": "tagline"}).content.decode()
    saved = client.post(url("save"), {**TAGLINE, "value_en": "Mine"}, **HTMX)

    assert "do not have permission" not in page
    assert saved.status_code == 200
    assert TextOverride.objects.get(key="tagline").value == "Mine"
