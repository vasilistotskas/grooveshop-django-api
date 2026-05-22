import os

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.fields.image import ImageAndSvgField
from core.models import TimeStampMixinModel


class ShippingProvider(TimeStampMixinModel):
    """DB-backed registry row, one per courier integration.

    Acts as the single on/off switch for a provider in checkout (via
    ``is_active``) and tells the abstraction which fulfilment kinds the
    provider can handle.

    The matching adapter class is looked up in the in-memory
    ``shipping.interfaces._REGISTRY`` by ``code``; the adapter is
    registered once at AppConfig.ready() time by each provider app.
    """

    code = models.SlugField(
        _("Code"),
        max_length=32,
        unique=True,
        help_text=_(
            "Stable identifier matching the registered carrier adapter "
            "(e.g. 'acs', 'boxnow')."
        ),
    )
    name = models.CharField(
        _("Name"),
        max_length=64,
        help_text=_("Display name shown to customers (e.g. 'ACS Courier')."),
    )
    is_active = models.BooleanField(
        _("Is Active"),
        default=False,
        help_text=_(
            "Master switch — when False the provider is hidden from "
            "checkout regardless of capability flags."
        ),
    )
    supports_home_delivery = models.BooleanField(
        _("Supports Home Delivery"),
        default=False,
    )
    supports_pickup_point = models.BooleanField(
        _("Supports Pickup Point"),
        default=False,
    )
    live_mode = models.BooleanField(
        _("Live Mode"),
        default=False,
        help_text=_(
            "False = sandbox / test credentials. Used by the admin UI to "
            "warn operators that vouchers won't actually ship."
        ),
    )
    priority = models.PositiveSmallIntegerField(
        _("Priority"),
        default=0,
        help_text=_("Sort order in checkout — lower numbers appear first."),
    )
    metadata = models.JSONField(
        _("Metadata"),
        default=dict,
        blank=True,
        help_text=_(
            "Provider-specific configuration (supported countries, "
            "feature flags, branding hints)."
        ),
    )
    logo = ImageAndSvgField(
        _("Logo"),
        upload_to="uploads/shipping/",
        blank=True,
        null=True,
        help_text=_(
            "Primary brand logo shown on the checkout shipping picker "
            "and the order summary — used for home-delivery rows and "
            "as the fallback for pickup-point rows when "
            "``logo_pickup_point`` is empty. PNG/JPG/SVG. Falls back "
            "to a shipped default in the storefront when blank, so a "
            "fresh deploy without uploaded assets still renders."
        ),
    )
    logo_pickup_point = ImageAndSvgField(
        _("Pickup-point logo"),
        upload_to="uploads/shipping/",
        blank=True,
        null=True,
        help_text=_(
            "Optional pickup-point-specific logo (e.g. a locker "
            "illustration distinct from the carrier's home-delivery "
            "brand mark). When set, the storefront's "
            "``/api/v1/shipping/options`` endpoint surfaces it on the "
            "pickup_point row's ``logoUrl`` so the locker card looks "
            "different from the home-delivery card for the same "
            "carrier. Falls back to ``logo`` when blank."
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Shipping Provider")
        verbose_name_plural = _("Shipping Providers")
        ordering = ["priority", "name"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["code"], name="shipping_provider_code_ix"),
            BTreeIndex(
                fields=["is_active", "priority"],
                name="shipping_provider_active_pri_ix",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    def supports(self, kind: str) -> bool:
        """Return True if this provider supports the given ShippingKind value."""
        if kind == "home_delivery":
            return self.supports_home_delivery
        if kind == "pickup_point":
            return self.supports_pickup_point
        return False

    @property
    def main_image_path(self) -> str:
        """Relative URL for the primary uploaded logo (mirrors ``PayWay.icon``).

        Empty string when no logo is uploaded — the storefront then
        falls back to its bundled default for the carrier.
        """
        if self.logo and hasattr(self.logo, "name"):
            return (
                f"media/uploads/shipping/"
                f"{os.path.basename(str(self.logo.name))}"
            )
        return ""

    @property
    def logo_filename(self) -> str:
        if self.logo and hasattr(self.logo, "name"):
            return os.path.basename(str(self.logo.name))
        return ""

    @property
    def logo_pickup_point_filename(self) -> str:
        if self.logo_pickup_point and hasattr(self.logo_pickup_point, "name"):
            return os.path.basename(str(self.logo_pickup_point.name))
        return ""

    def logo_for_kind(self, kind: str):
        """Return the ``FieldFile`` to show for a given ``ShippingKind``.

        ``pickup_point`` rows prefer ``logo_pickup_point`` and fall
        back to the primary ``logo`` when the pickup-specific variant
        isn't uploaded. Every other kind uses ``logo`` directly. The
        return is a ``FieldFile``-like object (or the empty-file
        sentinel) so callers can ``.url`` it the same way they would
        access either model field directly.
        """
        if (
            kind == "pickup_point"
            and self.logo_pickup_point
            and getattr(self.logo_pickup_point, "name", "")
        ):
            return self.logo_pickup_point
        return self.logo
