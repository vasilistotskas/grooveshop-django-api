from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html_join
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    RangeDateTimeFilter,
)

from admin.base import (
    BaseTranslatableAdmin,
)
from admin.displays import (
    change_link,
)
from product.models.product import (
    Product,
)
from product.models.variant_group import ProductVariantGroup


@admin.register(ProductVariantGroup)
class ProductVariantGroupAdmin(BaseTranslatableAdmin):
    """Admin for variant groups that link sibling product variations.

    Products are assigned to a group from the Product page (the
    ``variant_group`` autocomplete); this page is a registry of groups and an
    at-a-glance view of their members.
    """

    list_display = (
        "group_info",
        "active",
        "members_count_display",
        "created_at",
    )
    search_fields = [
        "id",
        "translations__name",
    ]
    list_filter = [
        "active",
        ("created_at", RangeDateTimeFilter),
    ]
    readonly_fields = (
        "id",
        "uuid",
        "created_at",
        "updated_at",
        "members_display",
    )
    # The Count() annotation in get_queryset strips the model's default
    # Meta.ordering (Django drops default ordering on GROUP BY queries),
    # which made the autocomplete paginator emit UnorderedObjectListWarning.
    # An explicit admin ordering survives the annotation.
    ordering = ["-created_at"]
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Variant Group"),
            {
                "fields": (
                    "name",
                    "active",
                ),
            },
        ),
        (
            _("Members"),
            {
                "fields": ("members_display",),
                "description": _(
                    "Products assigned to this group. Add or remove members "
                    "from each Product's page via the Variant Group field."
                ),
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "id",
                    "uuid",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        # Annotated count keeps the changelist to one query; the prefetch
        # feeds ``members_display`` on the change page.
        return (
            super()
            .get_queryset(request)
            .prefetch_related("translations", "variants__translations")
            .annotate(members_count=Count("variants", distinct=True))
        )

    @admin.display(description=_("Group"), ordering="translations__name")
    def group_info(self, obj):
        name = obj.safe_translation_getter("name", any_language=True) or _(
            "Unnamed"
        )
        return f"{name} (#{obj.id})"

    @admin.display(description=_("Members"), ordering="members_count")
    def members_count_display(self, obj):
        return obj.members_count

    @admin.display(description=_("Members"))
    def members_display(self, obj):
        variants = obj.variants.all()
        if not variants:
            return _("No products assigned yet.")
        links = (
            change_link(
                self.admin_site,
                Product,
                variant.pk,
                "{} (#{})".format(
                    variant.safe_translation_getter("name", any_language=True)
                    or variant.sku,
                    variant.pk,
                ),
            )
            for variant in variants
        )
        return format_html_join(
            "", '<span class="block">{}</span>', ((link,) for link in links)
        )
