import re

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from b2b.models import BusinessProfile
from b2b.validators import is_valid_greek_vat

VAT_PREFIX_RE = re.compile(r"^(EL|GR)", re.IGNORECASE)


class B2BErrorResponseSerializer(serializers.Serializer):
    detail = serializers.CharField(help_text=_("Human-readable message"))
    reason = serializers.CharField(
        help_text=_("Machine-readable reason, e.g. no_business_profile")
    )


class BusinessProfileSerializer(serializers.ModelSerializer):
    customer_group_name = serializers.CharField(
        source="customer_group.name",
        read_only=True,
        allow_null=True,
        default=None,
    )

    class Meta:
        model = BusinessProfile
        fields = (
            "uuid",
            "status",
            "customer_group_name",
            "company_name",
            "vat_id",
            "tax_office",
            "activity",
            "billing_street",
            "billing_street_number",
            "billing_city",
            "billing_zipcode",
            "vies_status",
            "vies_checked_at",
            "vies_name",
            "rejection_reason",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class BusinessProfileWriteSerializer(serializers.Serializer):
    company_name = serializers.CharField(max_length=255)
    vat_id = serializers.CharField(
        max_length=12,
        help_text=_("Greek ΑΦΜ — 9 digits, EL/GR prefix tolerated"),
    )
    tax_office = serializers.CharField(max_length=100)
    activity = serializers.CharField(max_length=255)
    billing_street = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    billing_street_number = serializers.CharField(
        max_length=50, required=False, allow_blank=True, default=""
    )
    billing_city = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default=""
    )
    billing_zipcode = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default=""
    )

    def validate_vat_id(self, value: str) -> str:
        cleaned = VAT_PREFIX_RE.sub("", value.strip().upper()).strip()
        if not is_valid_greek_vat(cleaned):
            raise serializers.ValidationError(
                _("Enter a valid Greek VAT number (ΑΦΜ).")
            )
        return cleaned


class B2BPriceSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    net_price = serializers.DecimalField(max_digits=11, decimal_places=2)
    final_price = serializers.DecimalField(max_digits=11, decimal_places=2)
    discount_percent = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text=_("Effective percent off the retail final price"),
    )
