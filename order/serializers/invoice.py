"""Read-only serializer for invoice download metadata.

The invoice PDF itself is stored in private storage; consumers receive
an absolute URL to a Django-auth-gated streaming endpoint rather than
a direct storage URL. This keeps the download contract identical on
S3 and FileSystem and makes the ownership check mandatory at the
HTTP boundary (no leaky presigned links, no dependence on Nuxt doing
the auth forwarding). The serializer also exposes the data the
frontend needs (invoice number, issue date, totals) to render a
"Download Invoice" button without a second round-trip.
"""

from __future__ import annotations

from django.urls import reverse
from rest_framework import serializers

from order.models.invoice import Invoice


class InvoiceDownloadResponseSerializer(serializers.ModelSerializer):
    """Invoice metadata plus an absolute URL to the streaming endpoint."""

    download_url = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()
    subtotal = serializers.SerializerMethodField()
    total_vat = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = (
            "invoice_number",
            "issue_date",
            "download_url",
            "subtotal",
            "total_vat",
            "total",
            "currency",
            "vat_breakdown",
        )
        read_only_fields = fields

    def get_download_url(self, obj: Invoice) -> str | None:
        """Absolute URL to ``OrderViewSet.invoice_download``.

        Routes every client click through Django so the same
        ``IsOwnerOrAdmin`` check applies to the bytes as to the
        metadata — avoids the pre-existing leak where
        ``document_file.url`` returned either an unsigned S3 URL
        (403s without IAM) or a broken ``/media/...`` path (not
        served in dev).
        """
        if not obj.has_document():
            return None
        request = self.context.get("request")
        path = reverse("order-invoice-download", kwargs={"pk": obj.order_id})
        if request is not None:
            return request.build_absolute_uri(path)
        return path

    def _money_amount(self, money_field) -> str | None:
        if money_field is None:
            return None
        amount = getattr(money_field, "amount", money_field)
        try:
            return str(amount)
        except Exception:  # noqa: BLE001
            return None

    def get_total(self, obj: Invoice) -> str | None:
        return self._money_amount(obj.total)

    def get_subtotal(self, obj: Invoice) -> str | None:
        return self._money_amount(obj.subtotal)

    def get_total_vat(self, obj: Invoice) -> str | None:
        return self._money_amount(obj.total_vat)
