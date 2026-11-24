from django import forms
from django.contrib import admin
from django.db import connection
from django.db.models import Count
from django.forms.models import BaseInlineFormSet
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext, pgettext_lazy
from unfold.admin import TabularInline
from unfold.decorators import display
from unfold.widgets import UnfoldAdminSelect2MultipleWidget

from admin.base import BaseModelAdmin
from admin.displays import header_two_line
from admin.filters import RelatedDropdownFilter
from pay_way.admin import PayWayShippingExclusionInline
from region.models import Region
from shipping.models import ShippingProvider, ShippingRate, ShippingZone
from shipping.zones import rate_kind_errors, zone_errors


class ShippingZoneAdminForm(forms.ModelForm):
    """The zone's country and regions, checked against the store's other
    zones (``shipping.zones.zone_errors``).

    ``regions`` goes through ``ShippingZoneRegion``, a through model of
    its own (its unique ``region`` keeps a region in one zone), which the
    admin refuses to edit as a model field (``admin.E013``);
    ``covered_regions`` stands in for it and
    ``ShippingZoneAdmin.save_related`` writes it.
    """

    covered_regions = forms.ModelMultipleChoiceField(
        queryset=Region.objects.select_related("country").prefetch_related(
            "translations", "country__translations"
        ),
        required=False,
        widget=UnfoldAdminSelect2MultipleWidget,
        label=_("Regions"),
        help_text=ShippingZone._meta.get_field("regions").help_text,
    )

    class Meta:
        model = ShippingZone
        fields = ("name", "country")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["covered_regions"].initial = list(
                self.instance.regions.values_list("pk", flat=True)
            )

    def clean(self):
        cleaned = super().clean()
        country = cleaned.get("country")
        regions = list(cleaned.get("covered_regions") or [])
        if country is not None:
            _lock_country_zones(country.pk)
        errors = zone_errors(
            zone=self.instance,
            country_code=country.pk if country is not None else None,
            regions=regions,
        )
        for field, messages in errors.items():
            for message in messages:
                self.add_error(
                    "covered_regions" if field == "regions" else field,
                    message,
                )
        return cleaned


def _lock_country_zones(country_code: str) -> None:
    """Serialise the saves of one country's zones until the admin's
    transaction ends (``changeform_view`` runs in one).

    "One whole-country zone per country" has no column a constraint could
    hold (a zone is whole-country by having no regions), so two staff
    saving one at the same moment would both pass ``zone_errors``. The
    second now waits here for the first to commit, then sees its zone.
    Advisory locks are per database, so the key names the store.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"{connection.schema_name}:shipping-zones:{country_code}"],
        )


class ShippingRateInlineFormSet(BaseInlineFormSet):
    """Refuses a pickup-point rate on a zone that covers only some
    regions (``shipping.zones.rate_kind_errors``).

    The zone's region set is saved after its rates are validated, so it
    is read from the zone form posted with them, not from the database.
    """

    def clean(self) -> None:
        super().clean()
        has_regions = bool(self.data.getlist("covered_regions"))
        kinds = [
            form.cleaned_data["kind"]
            for form in self.forms
            if form.cleaned_data
            and not form.cleaned_data.get("DELETE")
            and form.cleaned_data.get("kind")
        ]
        errors = rate_kind_errors(kinds, has_regions=has_regions)
        if errors:
            raise forms.ValidationError(errors)


class ShippingRateInline(TabularInline):
    """Per-(provider, kind) price, free-threshold and weight cap of a zone."""

    model = ShippingRate
    formset = ShippingRateInlineFormSet
    extra = 0
    tab = True
    fields = (
        "provider",
        "kind",
        "price",
        "free_shipping_threshold",
        "max_weight_grams",
        "delivery_days_min",
        "delivery_days_max",
        "is_active",
    )
    verbose_name = pgettext_lazy("shipping", "Rate")
    verbose_name_plural = _("Rates")


@admin.register(ShippingZone)
class ShippingZoneAdmin(BaseModelAdmin):
    """Where the store delivers: one country, whole or some regions, and
    the rates that apply there (``shipping.zones``)."""

    form = ShippingZoneAdminForm
    list_display = ("name", "country", "coverage", "rates_count")
    list_filter = (("country", RelatedDropdownFilter),)
    search_fields = ("name", "country__alpha_2")
    ordering = ("country_id", "name")
    list_select_related = ("country",)
    prefetch_translations = ("country__translations",)
    autocomplete_fields = ("country",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [ShippingRateInline]
    fieldsets = (
        (
            None,
            {
                "fields": ("name", "country", "covered_regions"),
                "description": _(
                    "A zone with no regions covers the whole country. A "
                    "zone with regions covers only those, and its rates "
                    "replace the whole-country zone's there: give the "
                    "islands their own price, or no home-delivery rate to "
                    "stop delivering there. Pickup-point rates belong to "
                    "the whole-country zone: a pickup point has no region."
                ),
            },
        ),
        (
            _("Timestamps"),
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def save_related(self, request, form, formsets, change) -> None:
        super().save_related(request, form, formsets, change)
        form.instance.regions.set(form.cleaned_data["covered_regions"])

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _rates_count=Count("rates", distinct=True),
                _regions_count=Count("regions", distinct=True),
            )
        )

    @admin.display(description=_("Covers"), ordering="_regions_count")
    def coverage(self, obj) -> str:
        if not obj._regions_count:
            return str(_("Whole country"))
        return ngettext(
            "%(count)d region", "%(count)d regions", obj._regions_count
        ) % {"count": obj._regions_count}

    @admin.display(description=_("Rates"), ordering="_rates_count")
    def rates_count(self, obj) -> int:
        return obj._rates_count


@admin.register(ShippingProvider)
class ShippingProviderAdmin(BaseModelAdmin):
    list_display = (
        "provider_display",
        "is_active",
        "supports_home_delivery",
        "supports_pickup_point",
        "live_mode",
        "priority",
        "rates_count",
    )
    list_filter = (
        "is_active",
        "live_mode",
        "supports_home_delivery",
        "supports_pickup_point",
    )
    search_fields = ("code", "name")
    ordering = ("priority", "name")
    readonly_fields = ("created_at", "updated_at")
    # Same inline as on PayWayAdmin — rows where this provider is the
    # FK target. Lets ops manage exclusions from whichever side they
    # land on (per-provider sweep vs per-pay-way sweep). Rates are edited
    # on their zone (``ShippingZoneAdmin``), where the destination is.
    inlines = [PayWayShippingExclusionInline]

    def get_queryset(self, request):
        return (
            super().get_queryset(request).annotate(_rates_count=Count("rates"))
        )

    @admin.display(description=_("Rates"), ordering="_rates_count")
    def rates_count(self, obj) -> int:
        return obj._rates_count

    @display(description=_("Carrier"), ordering="name", header=True)
    def provider_display(self, obj):
        """The primary logo; the pickup-point one is previewed on the
        change form."""
        return header_two_line(
            obj.name,
            obj.code,
            image_path=obj.logo.url if obj.logo else None,
            contained=True,
        )

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "code",
                    "name",
                    "is_active",
                    "priority",
                )
            },
        ),
        (
            _("Capabilities"),
            {
                "fields": (
                    "supports_home_delivery",
                    "supports_pickup_point",
                    "live_mode",
                )
            },
        ),
        (
            _("Branding"),
            {
                "fields": ("logo", "logo_pickup_point"),
                "description": _(
                    "Brand assets shown on the storefront's checkout "
                    "shipping picker and the order summary. "
                    "``Logo`` is used for home-delivery rows and as "
                    "the fallback for pickup-point rows. "
                    "``Pickup-point logo`` is optional — set it only "
                    "when you want the locker card to look different "
                    "from the home-delivery card for the same carrier "
                    "(e.g. an ACS Smartpoint locker illustration vs "
                    "the ACS courier mark). PNG/JPG/SVG. When both "
                    "are blank the storefront renders its bundled "
                    "default for the carrier."
                ),
            },
        ),
        (
            _("Metadata"),
            {
                "fields": ("metadata",),
                "classes": ("collapse",),
            },
        ),
        (
            _("Timestamps"),
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )
