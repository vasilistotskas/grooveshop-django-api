from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from rest_framework import serializers

from product.models.alert import ProductAlert, ProductAlertKind


class ProductAlertSerializer(serializers.ModelSerializer[ProductAlert]):
    target_price = MoneyField(
        max_digits=11, decimal_places=2, required=False, allow_null=True
    )
    # Override the ModelSerializer default for ``email``: the column is
    # declared ``EmailField(blank=True, default="")`` so the subscriber
    # can be identified by either the ``user`` FK OR an email. Declaring
    # the serializer field with ``allow_null=True`` (and no ``default``)
    # lets the OpenAPI contract expose the field as ``string | null``
    # instead of ``string`` with ``default: ""``. That matters for Zod
    # consumers: Zod 4 evaluates a schema's default BEFORE the format
    # check, so an empty-string default against ``z.email()`` fails —
    # the response from user-owned alerts (where email is naturally "")
    # would be rejected mid-parse even though Django returned 201.
    email = serializers.EmailField(
        required=False, allow_null=True, max_length=254
    )

    class Meta:
        model = ProductAlert
        fields = (
            "id",
            "uuid",
            "kind",
            "product",
            "user",
            "email",
            "target_price",
            "is_active",
            "notified_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "uuid",
            "user",
            "is_active",
            "notified_at",
            "created_at",
            "updated_at",
        )
        # Suppress the auto-generated UniqueTogetherValidators derived
        # from the model's `(kind, product, user)` and `(kind, product,
        # email)` constraints. Those validators have a side-effect of
        # forcing every referenced field to be treated as required on
        # input, which conflicts with our two-modes flow: authenticated
        # subscribers omit ``email`` (the user FK identifies them) while
        # guests omit nothing (they provide ``email``). Uniqueness is
        # still enforced end-to-end via the DB-level UniqueConstraints
        # — the view turns the resulting IntegrityError into a 409
        # response (`product/views/alert.py`).
        validators = []

    def validate(self, attrs):
        kind = attrs.get("kind")
        target_price = attrs.get("target_price")
        if kind == ProductAlertKind.PRICE_DROP and not target_price:
            raise serializers.ValidationError(
                {
                    "target_price": _(
                        "target_price is required for price-drop alerts."
                    )
                }
            )
        return attrs

    def to_representation(self, instance):
        """Emit ``null`` instead of the DB-side empty string.

        The column stores ``""`` for user-owned alerts so the compound
        uniqueness/partial indexes stay simple, but on the wire we
        surface that as ``null`` — it reads more honestly and keeps the
        OpenAPI contract aligned with ``nullable: true``.
        """
        data = super().to_representation(instance)
        if data.get("email") == "":
            data["email"] = None
        return data
