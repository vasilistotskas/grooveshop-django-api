"""The database itself refuses a product without a rate and a repeated rate.

``Product.vat`` is NOT NULL (``product`` 0070, the contract step of 0068 and
0069) and ``Vat.value`` is unique (``vat`` 0010). Both used to hold only in
code: a raw ``UPDATE`` or an ``update()`` cleared a product's rate, and a
second ``Vat`` row could repeat a rate, which made "the standard row" a
guess.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, connection, transaction

from product.factories import ProductFactory
from product.models.product import Product
from vat.models import Vat

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

D = Decimal


class TestAProductCannotLoseItsRate:
    def test_update_to_null_is_refused_by_the_database(self):
        product = ProductFactory(vat=Vat.objects.create(value=D("24.0")))

        with pytest.raises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=product.pk).update(vat=None)

    def test_raw_sql_is_refused_by_the_database(self):
        product = ProductFactory(vat=Vat.objects.create(value=D("24.0")))

        with pytest.raises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE product_product SET vat_id = NULL WHERE id = %s",
                    [product.pk],
                )

    def test_a_save_without_a_rate_is_refused(self):
        product = ProductFactory(vat=Vat.objects.create(value=D("24.0")))
        product.vat = None

        with pytest.raises((ObjectDoesNotExist, IntegrityError)):
            with transaction.atomic():
                product.save()


class TestARateIsUnique:
    def test_a_second_row_with_the_same_value_is_refused(self):
        Vat.objects.create(value=D("24.0"))

        with pytest.raises(IntegrityError), transaction.atomic():
            Vat.objects.create(value=D("24.0"))

    def test_a_different_value_is_accepted(self):
        Vat.objects.create(value=D("24.0"))

        Vat.objects.create(value=D("13.0"))

        assert Vat.objects.count() == 2

    def test_validation_names_the_duplicate(self):
        Vat.objects.create(value=D("24.0"))

        with pytest.raises(ValidationError) as caught:
            Vat(value=D("24.0")).full_clean()

        assert "value" in caught.value.message_dict or (
            "__all__" in caught.value.message_dict
        )

    def test_a_row_does_not_clash_with_itself(self):
        vat = Vat.objects.create(value=D("24.0"))

        vat.full_clean()


class TestTheAdminFormRefusesARepeatedRate:
    """The Vat admin's form gives a form error, not a 500 IntegrityError."""

    @staticmethod
    def _form(data, instance=None):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory

        from user.factories.account import UserAccountFactory
        from vat.admin import VatAdmin

        request = RequestFactory().get("/")
        request.user = UserAccountFactory(
            num_addresses=0, is_superuser=True, is_staff=True
        )
        model_admin = VatAdmin(Vat, AdminSite())
        form_class = model_admin.get_form(request, instance, change=False)
        return form_class(data, instance=instance)

    def test_a_repeated_value_is_a_form_error(self):
        Vat.objects.create(value=D("24.0"))

        form = self._form({"value": "24.0"})

        assert not form.is_valid()
        assert "already exists" in " ".join(form.non_field_errors())

    def test_a_new_value_is_valid(self):
        Vat.objects.create(value=D("24.0"))

        assert self._form({"value": "13.0"}).is_valid()

    def test_editing_a_row_keeps_its_own_value(self):
        vat = Vat.objects.create(value=D("24.0"))

        assert self._form({"value": "24.0"}, instance=vat).is_valid()
