"""Cross-schema audit attribution must not be a database constraint.

``HistoryRequestMiddleware`` attributes writes to ``request.user`` — on
a tenant host that is a PLATFORM-PUBLIC identity — while
``product_historicalproduct.history_user`` and ``product.changed_by``
live in the TENANT schema, where the user table is the tenant copy. A
real FK there only ever held because the cutover copied users
id-preserving: a post-cutover staff identity (public-only, no tenant
row) saving a product raised an FK violation at INSERT.

The ACS/BoxNow shipment histories already declare
``user_db_constraint=False`` for exactly this reason; Product was the
one historied model that had been missed. These tests pin the fix by
doing what the bug forbade: attributing to a user id that exists in no
user table at all.
"""

from __future__ import annotations

import pytest

from product.factories.product import ProductFactory
from product.models.product import Product
from user.models import UserAccount

# An id far beyond anything the test databases allocate.
GHOST_PK = 987_654_321


@pytest.mark.django_db
class TestCrossSchemaAttribution:
    def test_changed_by_may_reference_a_missing_user(self):
        """Previously an IntegrityError at COMMIT."""
        product = ProductFactory()
        product.changed_by = UserAccount(pk=GHOST_PK)
        product.save(update_fields=["changed_by"])

        product.refresh_from_db()
        assert product.changed_by_id == GHOST_PK

    def test_history_user_may_reference_a_missing_user(self):
        """The historical INSERT is where the constraint actually fired."""
        product = ProductFactory()
        product._history_user = UserAccount(pk=GHOST_PK)
        product.stock = product.stock + 1
        product.save(update_fields=["stock"])

        row = product.history.first()
        assert row.history_user_id == GHOST_PK

    def test_attribution_stays_readable_through_the_orm(self):
        """db_constraint=False keeps the relation usable — a resolvable
        id still resolves; only enforcement is gone."""
        product = ProductFactory()
        actor = UserAccount.objects.create_user(
            email="attribution-actor@example.com",
            username="attributionactor",
            password="x",
        )
        product.changed_by = actor
        product.save(update_fields=["changed_by"])

        product.refresh_from_db()
        assert product.changed_by == actor

    def test_the_model_declares_the_orm_only_relations(self):
        """Regression guard at the declaration level.

        A future field rewrite that drops these flags reintroduces the
        FK at the next migration — fail here, not in production.
        """
        changed_by = Product._meta.get_field("changed_by")
        assert changed_by.db_constraint is False

        history_model = Product.history.model
        history_user = history_model._meta.get_field("history_user")
        assert history_user.db_constraint is False
