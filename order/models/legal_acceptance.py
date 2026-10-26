from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from order.enum.legal_acceptance import LegalAcceptanceSource


class OrderLegalAcceptance(models.Model):
    """The version of one legal document an order was placed under.

    Written in the order's creation transaction, one row per document
    the buyer accepts by ordering (``CHECKOUT_CONSENT_SLUGS``), by
    ``OrderLegalAcceptanceService.record``. Never edited. The version
    holds the exact text, so a later edit to the page does not change
    what this order records.
    """

    order = models.ForeignKey(
        "order.Order",
        on_delete=models.CASCADE,
        related_name="legal_acceptances",
        verbose_name=_("Order"),
    )
    document = models.ForeignKey(
        "page_config.LegalDocumentVersion",
        on_delete=models.PROTECT,
        related_name="order_acceptances",
        verbose_name=_("Document version"),
    )
    source = models.CharField(
        _("Accepted through"),
        max_length=32,
        choices=LegalAcceptanceSource.choices,
    )
    accepted_at = models.DateTimeField(_("Accepted at"), auto_now_add=True)

    class Meta(TypedModelMeta):
        verbose_name = _("Legal acceptance")
        verbose_name_plural = _("Legal acceptances")
        ordering = ["order", "document__page__slug"]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "document"],
                name="order_legal_acceptance_unique_document",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.order_id}: {self.document}"
