from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel
from order.models.attribution import AcquisitionSourceModel


class CartAttribution(AcquisitionSourceModel):
    """Where the shopper who started a cart came from: its first touch.

    Written by ``CartAttributionService.capture`` on the add that takes a
    cart from no lines to some, and never rewritten while the cart keeps
    them. The order built from the cart keeps its own checkout source and
    copies this one next to it (``OrderAttribution.cart_source``), since
    the cart is deleted once its lines become an order. A cart that
    predates this table has no row.
    """

    cart = models.OneToOneField(
        "cart.Cart",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="attribution",
        verbose_name=_("Cart"),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Cart attribution")
        verbose_name_plural = _("Cart attributions")
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["source_type"], name="cart_attr_type_ix"),
            BTreeIndex(fields=["source"], name="cart_attr_source_ix"),
        ]

    def __str__(self) -> str:
        return f"{self.cart_id}: {self.source_type} {self.source}".strip()
