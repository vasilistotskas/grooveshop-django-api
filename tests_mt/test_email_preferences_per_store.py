"""A customer's email choices belong to the store they were made in."""

from __future__ import annotations

import pytest
from django_tenants.utils import schema_context

from emails import optout
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut
from tests.utils.emails import open_email_gates  # noqa: F401
from user.models import UserAccount
from user.services.subscription import (
    marketing_preferences,
    set_marketing_preferences,
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("open_email_gates"),
]

ADDRESS = "same-person@example.com"
RESTOCK = "product.restock_alert"


@pytest.fixture(autouse=True)
def _cleanup(mt_tenant, mt_tenant_b):
    yield
    for tenant in (mt_tenant, mt_tenant_b):
        with schema_context(tenant.schema_name):
            EmailOptOut.objects.all().delete()
            UserAccount.objects.filter(email=ADDRESS).delete()


def _enabled(address: str) -> dict[str, bool]:
    return {p.email_type.key: p.enabled for p in marketing_preferences(address)}


def test_lifting_an_opt_out_in_one_store_leaves_the_other_store_opted_out(
    mt_tenant, mt_tenant_b
):
    accounts = {}
    for tenant in (mt_tenant, mt_tenant_b):
        with schema_context(tenant.schema_name):
            accounts[tenant.schema_name] = UserAccount.objects.create_user(
                email=ADDRESS,
                username=f"u-{tenant.schema_name}"[:30],
                password=None,
            )
            optout.opt_out(
                ADDRESS, EVERY_MARKETING_TYPE, source=EmailOptOut.Source.LINK
            )

    with schema_context(mt_tenant.schema_name):
        set_marketing_preferences(
            ADDRESS,
            account_pk=accounts[mt_tenant.schema_name].pk,
            choices={RESTOCK: True},
        )
        assert _enabled(ADDRESS)[RESTOCK] is True

    with schema_context(mt_tenant_b.schema_name):
        assert _enabled(ADDRESS)[RESTOCK] is False, (
            "store A's choice reached store B: the rows are not schema-scoped"
        )
        assert EmailOptOut.objects.filter(
            type_key=EVERY_MARKETING_TYPE
        ).exists()
