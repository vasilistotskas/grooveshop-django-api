from django.contrib import admin, messages
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.text import Truncator
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from unfold.contrib.filters.admin import (
    RangeDateTimeFilter,
    SliderNumericFilter,
)
from unfold.dataclasses import ActionDialog
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from admin.base import (
    BaseTranslatableAdmin,
)
from admin.displays import (
    REVIEW_STATUS_VARIANT,
    change_link,
    choice_label,
    format_dt,
    header_two_line,
)
from admin.responses import hx_redirect
from product.admin.filters import ReviewStatusFilter
from product.enum.review import ReviewStatus
from product.models.image import ProductImage
from product.models.review import ProductReview
from user.models.account import UserAccount

#: How much of a review the changelist quotes; the change page has it all.
COMMENT_EXCERPT_CHARS = 80


@admin.register(ProductReview)
class ProductReviewAdmin(BaseTranslatableAdmin):
    show_full_result_count = False  # Disable expensive COUNT(*) query

    list_display = (
        "product_header",
        "reviewer",
        "rating_display",
        "comment_excerpt",
        "status_label",
        "verified_purchase_display",
        "created_display",
    )
    date_hierarchy = "created_at"
    list_filter = [
        ReviewStatusFilter,
        ("rate", SliderNumericFilter),
        ("created_at", RangeDateTimeFilter),
        # Removed RelatedDropdownFilter for product and user - too expensive with 1.2M products
        # Use search instead to find specific products/users
    ]
    actions = [
        "approve_reviews",
        "reject_reviews",
    ]
    actions_detail = ["approve_review", "reject_review"]
    search_fields = [
        "user__email",
        "user__username",
        # Removed translations__comment search - too expensive with 100k+ reviews
        # Removed product__translations__name - too expensive with 1.2M products
    ]
    list_select_related = ["product", "user"]
    autocomplete_fields = ("product", "user")
    readonly_fields = (
        "created_at",
        "updated_at",
        "uuid",
        "is_published",
        "published_at",
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        # ``product__translations`` and the main-image prefetch keep
        # ``product_header`` and ``comment_excerpt`` from querying per
        # row; the verified-purchase flag is one annotation.
        qs = (
            qs.select_related("product", "user")
            .prefetch_related(
                "translations",
                "product__translations",
                Prefetch(
                    "product__images",
                    queryset=ProductImage.objects.filter(is_main=True),
                    to_attr="_prefetched_main_images",
                ),
            )
            .with_verified_purchase()
        )
        if not self.serves_changelist(request):
            # The change form edits and saves the whole row.
            return qs
        return qs.only(
            # Review fields - minimal set
            "id",
            "product_id",
            "user_id",
            "rate",
            "status",
            "created_at",
            # Product fields
            "product__id",
            "product__slug",
            # User fields
            "user__id",
            "user__email",
            "user__username",
            "user__first_name",
            "user__last_name",
        )

    fieldsets = (
        (
            _("Review Information"),
            {
                "fields": ("product", "user", "rate", "status"),
            },
        ),
        (
            _("Content"),
            {
                "fields": ("comment",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "is_published",
                    "published_at",
                    "uuid",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    @display(description=_("Product"), header=True)
    def product_header(self, obj):
        name = obj.product.safe_translation_getter(
            "name", any_language=True
        ) or str(obj.product_id)
        # The image's URL, not ``main_image_path``: that is the storage path
        # the storefront hands to media-stream, and the admin page resolved
        # it relative to itself (``…/productreview/media/…``, a 404).
        main_images = getattr(obj.product, "_prefetched_main_images", [])
        main_image = main_images[0] if main_images else None
        return header_two_line(
            name,
            _("Review #%(id)s") % {"id": obj.id},
            image_path=(
                main_image.image.url
                if main_image and main_image.image
                else None
            ),
            squared=True,
        )

    @admin.display(description=_("Customer"))
    def reviewer(self, obj):
        return change_link(
            self.admin_site, UserAccount, obj.user_id, obj.reviewer_label
        )

    @admin.display(description=_("Comment"))
    def comment_excerpt(self, obj):
        comment = obj.safe_translation_getter("comment", any_language=True)
        if not comment:
            return _("No comment")
        return Truncator(comment).chars(COMMENT_EXCERPT_CHARS)

    @admin.display(description=_("Rating"), ordering="rate")
    def rating_display(self, obj):
        return obj.stars

    @admin.display(description=_("Verified purchase"), boolean=True)
    def verified_purchase_display(self, obj):
        return obj.is_verified_purchase

    status_label = choice_label(
        "status", variants=REVIEW_STATUS_VARIANT, description=_("Status")
    )

    @admin.display(description=_("Created"), ordering="created_at")
    def created_display(self, obj):
        return format_dt(obj.created_at)

    @action(
        description=_("Approve selected reviews"),
        permissions=["change"],
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def approve_reviews(self, request, queryset):
        updated = queryset.moderate(ReviewStatus.TRUE)
        self.message_user(
            request,
            ngettext(
                "%(count)d review was approved.",
                "%(count)d reviews were approved.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=_("Reject selected reviews"),
        permissions=["change"],
        variant=ActionVariant.DANGER,
        icon="cancel",
    )
    def reject_reviews(self, request, queryset):
        updated = queryset.moderate(ReviewStatus.FALSE)
        self.message_user(
            request,
            ngettext(
                "%(count)d review was rejected.",
                "%(count)d reviews were rejected.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    def _back_to(self, object_id):
        return hx_redirect(
            reverse(
                f"{self.admin_site.name}:product_productreview_change",
                args=[object_id],
            )
        )

    @action(
        description=_("Approve"),
        permissions=["change"],
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
        dialog=ActionDialog(
            title=_("Approve the review"),
            description=_("Publishes the review on the product page."),
        ),
    )
    def approve_review(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        ProductReview.objects.filter(pk=object_id).moderate(ReviewStatus.TRUE)
        self.message_user(
            request, _("The review was approved."), messages.SUCCESS
        )
        return self._back_to(object_id)

    @action(
        description=_("Reject"),
        permissions=["change"],
        variant=ActionVariant.DANGER,
        icon="cancel",
        dialog=ActionDialog(
            title=_("Reject the review"),
            description=_("Hides the review from the product page."),
        ),
    )
    def reject_review(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        ProductReview.objects.filter(pk=object_id).moderate(ReviewStatus.FALSE)
        self.message_user(
            request, _("The review was rejected."), messages.SUCCESS
        )
        return self._back_to(object_id)
