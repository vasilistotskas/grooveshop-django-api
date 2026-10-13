"""``Tenant.clean()`` — Google Ads conversion tracking fields.

The conversion ID and the per-action labels are what the storefront
turns into ``gtag('event', 'conversion', {send_to: 'AW-…/LABEL'})``.
A label without an ID is dead configuration that LOOKS armed, so the
model refuses it rather than letting the storefront silently send
nothing.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from tenant.admin import TenantAdmin
from tenant.models import Tenant
from tenant.serializers import TenantAdminSerializer, TenantConfigSerializer


def _unsaved_tenant(**kwargs) -> Tenant:
    defaults = {
        "schema_name": "ads_clean_test",
        "name": "Ads Clean Test",
        "slug": "ads-clean-test",
        "owner_email": "owner@ads.example.com",
    }
    defaults.update(kwargs)
    return Tenant(**defaults)


class TestConversionId:
    def test_empty_is_valid(self) -> None:
        _unsaved_tenant(google_ads_conversion_id="").clean()

    def test_aw_prefix_with_digits_is_valid(self) -> None:
        _unsaved_tenant(google_ads_conversion_id="AW-18429554292").clean()

    @pytest.mark.parametrize(
        "value",
        [
            "18429554292",  # bare id
            "G-GGKMZ8YLNN",  # a GA4 id in the Ads slot
            "AW-",  # no digits
            "AW-18429554292/7T1qCJOa0vocEPTc8tNE",  # the whole send_to
            "aw-18429554292",  # wrong case
        ],
    )
    def test_anything_else_raises(self, value: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _unsaved_tenant(google_ads_conversion_id=value).clean()
        assert "google_ads_conversion_id" in exc_info.value.message_dict


class TestConversionLabels:
    ID = "AW-18429554292"

    @pytest.mark.parametrize("field", Tenant.GOOGLE_ADS_LABEL_FIELDS)
    def test_a_label_with_the_id_is_valid(self, field: str) -> None:
        _unsaved_tenant(
            google_ads_conversion_id=self.ID, **{field: "7T1qCJOa0vocEPTc8tNE"}
        ).clean()

    @pytest.mark.parametrize("field", Tenant.GOOGLE_ADS_LABEL_FIELDS)
    def test_a_label_without_the_id_is_refused(self, field: str) -> None:
        # The label is the second half of send_to; alone it can never be
        # sent, and the operator who typed it expects conversions.
        with pytest.raises(ValidationError) as exc_info:
            _unsaved_tenant(**{field: "7T1qCJOa0vocEPTc8tNE"}).clean()
        assert field in exc_info.value.message_dict

    def test_the_whole_send_to_is_refused_as_a_label(self) -> None:
        # The most likely paste: Google's snippet shows 'AW-…/LABEL'.
        with pytest.raises(ValidationError) as exc_info:
            _unsaved_tenant(
                google_ads_conversion_id=self.ID,
                google_ads_purchase_label="AW-18429554292/7T1qCJOa0vocEPTc8tNE",
            ).clean()
        assert "google_ads_purchase_label" in exc_info.value.message_dict

    def test_every_bad_label_is_reported_at_once(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _unsaved_tenant(
                google_ads_purchase_label="ok_label",
                google_ads_page_view_label="also-ok",
            ).clean()
        assert set(exc_info.value.message_dict) >= {
            "google_ads_purchase_label",
            "google_ads_page_view_label",
        }

    def test_the_id_alone_is_valid(self) -> None:
        # Provisioned but no actions yet — a legitimate intermediate
        # state while the campaign is being set up.
        _unsaved_tenant(google_ads_conversion_id=self.ID).clean()


class TestAddPaymentInfoLabel:
    def test_is_one_of_the_validated_label_fields(self) -> None:
        assert (
            "google_ads_add_payment_info_label"
            in Tenant.GOOGLE_ADS_LABEL_FIELDS
        )

    def test_defaults_to_empty_in_the_database_too(self) -> None:
        # ``db_default`` keeps the column writable by the previous
        # release's INSERTs during the PreSync rollout.
        field = Tenant._meta.get_field("google_ads_add_payment_info_label")
        assert field.db_default == ""

    def test_resolve_payload_field_is_optional(self) -> None:
        # Read-only would be REQUIRED in the schema, and a storefront
        # deployed ahead of this backend would reject every resolve.
        field = TenantConfigSerializer().fields[
            "google_ads_add_payment_info_label"
        ]
        assert not field.read_only
        assert not field.required

    def test_resolve_payload_reads_the_model_value(self) -> None:
        tenant = _unsaved_tenant(
            google_ads_add_payment_info_label="7T1qCJOa0vocEPTc8tNE"
        )
        field = TenantConfigSerializer().fields[
            "google_ads_add_payment_info_label"
        ]
        assert field.get_attribute(tenant) == "7T1qCJOa0vocEPTc8tNE"

    def test_admin_serializer_exposes_it(self) -> None:
        assert (
            "google_ads_add_payment_info_label"
            in TenantAdminSerializer.Meta.fields
        )

    def test_is_in_the_marketing_fieldset(self) -> None:
        fields = [
            name
            for _title, options in TenantAdmin.fieldsets
            for name in options["fields"]
        ]
        assert "google_ads_add_payment_info_label" in fields
