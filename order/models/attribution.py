from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel
from order.attribution import (
    CAMPAIGN_MAX_LENGTH,
    LANDING_PATH_MAX_LENGTH,
    MEDIUM_MAX_LENGTH,
    REFERRER_HOST_MAX_LENGTH,
    SOURCE_MAX_LENGTH,
)
from order.enum.attribution import OrderSourceType


class OrderAttribution(TimeStampMixinModel):
    """Where the shopper behind an order came from.

    Written once, in the order's creation transaction, by
    ``OrderAttributionService.record`` from ``order.attribution.classify``.
    Its own table rather than columns on ``Order`` so the order row
    stays about the sale, and so later attribution data (device,
    session count) has somewhere to go. Orders placed before it existed
    have no row.

    Shopper-supplied analytics: a UTM can be typed by hand, so nothing
    here is ever an authorisation signal. Only the referrer's host is
    kept and click-id values never reach the server.
    """

    order = models.OneToOneField(
        "order.Order",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="attribution",
        verbose_name=_("Order"),
    )
    source_type = models.CharField(
        _("Source type"),
        max_length=16,
        choices=OrderSourceType.choices,
        default=OrderSourceType.DIRECT,
    )
    source = models.CharField(
        _("Order source"),
        max_length=SOURCE_MAX_LENGTH,
        blank=True,
        default="",
        help_text=_(
            "A known channel's key (e.g. ``instagram``), the referring "
            "host, or the raw ``utm_source``. Empty for a direct visit."
        ),
    )
    medium = models.CharField(
        _("Traffic medium"),
        max_length=MEDIUM_MAX_LENGTH,
        blank=True,
        default="",
    )
    campaign = models.CharField(
        _("Campaign name"),
        max_length=CAMPAIGN_MAX_LENGTH,
        blank=True,
        default="",
    )
    referrer_host = models.CharField(
        _("Referrer host"),
        max_length=REFERRER_HOST_MAX_LENGTH,
        blank=True,
        default="",
    )
    landing_path = models.CharField(
        _("Landing page"),
        max_length=LANDING_PATH_MAX_LENGTH,
        blank=True,
        default="",
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Order attribution")
        verbose_name_plural = _("Order attributions")
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["source_type"], name="order_attr_type_ix"),
            BTreeIndex(fields=["source"], name="order_attr_source_ix"),
        ]

    def __str__(self) -> str:
        return f"{self.order_id}: {self.source_type} {self.source}".strip()
