"""A staff member named on a store row is never read as a shopper.

Staff are PUBLIC-schema accounts; the rows they act on live in the store's
schema, whose own ``user_useraccount`` holds SHOPPERS. A column that was a
foreign key to the user table resolved a staff id against the shoppers, so a
shopper with the same id was shown as the actor (and without a shopper the
write failed). The columns are now plain ids resolved in ``public``
(``tenant.staff_actors``). The main suite has one user table and cannot see
either half.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django_tenants.utils import get_public_schema_name, schema_context

from tests.utils.staff import stamp_platform_identity

pytestmark = pytest.mark.django_db

GHOST_PK = 987_654_321


@pytest.fixture
def staff():
    from user.models import UserAccount

    with schema_context(get_public_schema_name()):
        user = UserAccount.objects.create_user(
            email="mt-actor-staff@example.com",
            username="mtactorstaff",
            password="x-passw0rd!",
            is_staff=True,
        )
    return stamp_platform_identity(user)


@pytest.fixture
def shopper_with_the_staff_id(mt_tenant, staff):
    """A shopper of the store who holds the staff member's id."""
    from user.models import UserAccount

    with schema_context(mt_tenant.schema_name):
        shopper = UserAccount(
            pk=staff.pk,
            email="mt-actor-shopper@example.com",
            username="mtactorshopper",
        )
        shopper.set_password("x-passw0rd!")
        shopper.save()
    return shopper


@pytest.fixture
def product(mt_tenant):
    from product.factories.product import ProductFactory

    with schema_context(mt_tenant.schema_name):
        yield ProductFactory.create(stock=10, num_images=0, num_reviews=0)


def test_the_stock_log_names_the_staff_member_not_the_shopper(
    mt_tenant, staff, shopper_with_the_staff_id, product
):
    from order.models.stock_log import StockLog
    from order.stock import StockManager

    with schema_context(mt_tenant.schema_name):
        StockManager.adjust_stock(product, 3, performed_by=staff)

        log = (
            StockLog.objects.prefetch_related("performed_by_staff")
            .select_related("performed_by")
            .get(product=product)
        )

        assert log.performed_by_staff_id == staff.pk
        assert log.actor.email == staff.email
        assert log.actor.email != shopper_with_the_staff_id.email
        # Not a shopper's reservation: the shopper column stays empty.
        assert log.performed_by is None


def test_a_page_of_rows_resolves_its_staff_in_one_public_query(
    mt_tenant, staff, product
):
    from django.test.utils import CaptureQueriesContext

    from order.models.stock_log import StockLog
    from order.stock import StockManager

    with schema_context(mt_tenant.schema_name):
        for _ in range(3):
            StockManager.adjust_stock(product, 1, performed_by=staff)

        with CaptureQueriesContext(connection) as queries:
            logs = list(
                StockLog.objects.filter(product=product).prefetch_related(
                    "performed_by_staff"
                )
            )
            assert {log.actor.email for log in logs} == {staff.email}

    users = [q for q in queries if '"user_useraccount"' in q["sql"]]
    assert len(users) == 1, [q["sql"] for q in users]


def test_the_product_history_names_the_staff_member_not_the_shopper(
    mt_tenant, staff, shopper_with_the_staff_id, product
):
    with schema_context(mt_tenant.schema_name):
        product._history_user = staff
        product.stock = product.stock + 1
        product.save()

        row = product.history.first()
        product.refresh_from_db()

        assert row.history_user_id == staff.pk
        assert row.history_user.email == staff.email
        assert product.changed_by.email == staff.email


def test_a_staff_id_the_store_has_no_user_for_is_stored(mt_tenant):
    """The old constraint failed here (or, worse, passed because a shopper
    held the id). Checked at COMMIT in production, so force it now."""
    from b2b.factories import BusinessProfileFactory

    with schema_context(mt_tenant.schema_name):
        profile = BusinessProfileFactory.create()
        type(profile).objects.filter(pk=profile.pk).update(
            reviewed_by_id=GHOST_PK
        )
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

        profile.refresh_from_db()
        assert profile.reviewed_by_id == GHOST_PK


def test_a_shopper_on_the_api_is_not_recorded_as_staff(mt_tenant, product):
    """Only the staff backend stamps an identity: a shopper's id read as a
    staff id would name an unrelated staff member."""
    from order.stock import StockManager
    from user.models import UserAccount

    with schema_context(mt_tenant.schema_name):
        shopper = UserAccount(
            email="mt-actor-api-shopper@example.com",
            username="mtactorapishopper",
        )
        shopper.set_password("x-passw0rd!")
        shopper.save()

        StockManager.adjust_stock(product, 1, performed_by=shopper)

        from order.models.stock_log import StockLog

        assert (
            StockLog.objects.get(product=product).performed_by_staff_id is None
        )


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("b2b_business_profile", "reviewed_by_id"),
        ("giftcard_transaction", "created_by_id"),
        ("loyalty_points_transaction", "created_by_id"),
    ],
)
def test_the_store_schema_has_no_foreign_key_on_a_staff_column(
    mt_tenant, table, column
):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
            WHERE rel.relname = %s AND nsp.nspname = %s
              AND con.contype = 'f'
              AND pg_get_constraintdef(con.oid) LIKE %s
            """,
            [table, mt_tenant.schema_name, f"FOREIGN KEY ({column})%"],
        )
        assert cursor.fetchone() is None
