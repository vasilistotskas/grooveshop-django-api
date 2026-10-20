"""Cross-schema audit attribution is a plain staff id, not a relation.

``HistoryRequestMiddleware`` attributes writes to ``request.user`` - on a
tenant host a PLATFORM-PUBLIC identity - while the history row and
``product.changed_by`` live in the TENANT schema, where the user table
holds the store's shoppers. A relation there either violates (no such
row) or names a shopper who shares the id, so the columns hold the id and
``tenant.staff_actors`` resolves it in public. The store-schema half of
this is ``tests_mt/test_staff_actor_resolution.py``.
"""

from __future__ import annotations

import pytest

from product.factories.product import ProductFactory
from product.models.product import Product
from tests.utils.staff import stamp_platform_identity
from user.factories import UserAccountFactory

# An id far beyond anything the test databases allocate.
GHOST_PK = 987_654_321


@pytest.mark.django_db
class TestStaffAttribution:
    def test_changed_by_may_name_a_missing_account(self):
        product = ProductFactory()
        product.changed_by_id = GHOST_PK
        product.save(update_fields=["changed_by_id"])

        product.refresh_from_db()
        assert product.changed_by_id == GHOST_PK
        assert product.changed_by.pk == GHOST_PK
        assert str(product.changed_by) == ""

    def test_history_user_may_name_a_missing_account(self):
        product = ProductFactory()
        product.changed_by_id = GHOST_PK
        product.stock = product.stock + 1
        product.save(update_fields=["changed_by_id", "stock"])

        row = product.history.first()
        assert row.history_user_id == GHOST_PK

    def test_an_assigned_staff_member_resolves(self):
        product = ProductFactory()
        actor = stamp_platform_identity(UserAccountFactory())
        product.changed_by = actor
        product.save(update_fields=["changed_by_id"])

        product.refresh_from_db()
        assert product.changed_by == actor

    def test_a_shopper_is_never_recorded_as_staff(self):
        """Only the staff backend stamps an identity; anyone else (a
        shopper acting through the API) leaves the column empty rather
        than naming an unrelated staff member."""
        product = ProductFactory()
        product.changed_by = UserAccountFactory()
        product.save(update_fields=["changed_by_id"])

        product.refresh_from_db()
        assert product.changed_by_id is None
        assert product.changed_by is None

    def test_the_columns_are_ids_not_relations(self):
        """A relation to the store's user table is what named the wrong
        person: it must not come back."""
        assert not Product._meta.get_field("changed_by_id").is_relation
        history_model = Product.history.model
        assert not history_model._meta.get_field("history_user_id").is_relation
