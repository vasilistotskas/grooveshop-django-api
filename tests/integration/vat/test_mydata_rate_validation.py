"""A ``Vat`` row cannot be saved with a rate myDATA cannot map.

Regression coverage for the production incident where a live tenant
ran products at 23% (invalid since June 2016) — a rate absent from
``order/mydata/builder.py``'s ``_VAT_CATEGORY_BY_RATE``, which would
have raised ``ValueError`` for every order the moment myDATA was
enabled. ``Vat.value``'s ``validate_mydata_vat_rate`` validator now
rejects it up front, at row-save time, via ``full_clean()`` — the same
path Django admin forms already run through.

Note: bypassing validation with a bare ``.objects.create()`` (the way
``VatFactory``/``django_get_or_create`` already behaves for the
existing ``MinValueValidator``/``MaxValueValidator`` on this field) is
unaffected by design — Django never calls ``full_clean()`` on
``save()`` — so this only tightens the admin/serializer path, exactly
per the design brief.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from vat.constants import MYDATA_SUPPORTED_VAT_RATES
from vat.models import Vat


@pytest.mark.django_db
class TestMydataVatRateValidation:
    def test_rejects_a_rate_myDATA_cannot_map(self):
        vat = Vat(value=Decimal("23.0"))

        with pytest.raises(ValidationError) as exc_info:
            vat.full_clean()

        assert "value" in exc_info.value.message_dict
        message = " ".join(exc_info.value.message_dict["value"])
        assert "23" in message
        # The allowed rates must be named so an operator can fix the
        # row without guessing.
        assert "24" in message

    @pytest.mark.parametrize("rate", sorted(MYDATA_SUPPORTED_VAT_RATES))
    def test_accepts_every_mydata_supported_rate(self, rate):
        Vat(value=rate).full_clean()

    def test_admin_form_full_clean_is_the_enforcement_path(self):
        """A row created via the bare ORM (bypassing full_clean, same
        as any Django model) is untouched — only forms/serializers that
        run full_clean() enforce this."""
        vat = Vat.objects.create(value=Decimal("23.0"))
        assert vat.pk is not None
