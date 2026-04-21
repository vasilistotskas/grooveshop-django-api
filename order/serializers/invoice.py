"""Read-only serializer for invoice download metadata.

The invoice PDF itself is stored in private storage; consumers receive
a short-lived signed URL rather than the file bytes. The serializer
exposes enough data for the frontend to render a "Download Invoice"
button (invoice number, issue date, total) without an extra round-trip.
"""

from __future__ import annotations

from rest_framework import serializers

from order.models.invoice import Invoice


class InvoiceDownloadResponseSerializer(serializers.ModelSerializer):
    """Invoice metadata plus a short-lived signed download URL."""

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
        """Return a signed, short-lived URL for the PDF.

        On S3 backends django-storages already signs the URL when
        ``default_acl='private'`` and ``AWS_QUERYSTRING_AUTH=True`` —
        otherwise the storage backend's ``.url()`` returns the relative
        path which the Nuxt layer can still fetch through its own
        authenticated proxy.
        """
        if not obj.has_document():
            return None
        try:
            return obj.document_file.url
        except Exception:  # noqa: BLE001 — storage backends may vary
            return None

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
