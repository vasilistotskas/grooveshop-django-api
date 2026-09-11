"""Admin for the recommendation engine.

Three surfaces, three postures:

* ``RecommendationSlot`` is the one merchants EDIT — chain, weights,
  limits per surface. Validation lives on the model's ``clean()`` so a
  bad chain is a field error, not a 500.
* ``RecommendationCandidate`` is read-only: it is Celery's output, and
  editing a row by hand would be overwritten by the next rebuild. It is
  here so "why did it show that" can be answered without a shell.
* ``RecommendationEvent`` is read-only for the same reason; it is the
  raw feedback the nightly aggregation reads.

``BaseModelAdmin`` keeps all three off the platform console: these are
tenant-app models and the base withholds them on the public schema.
"""

from __future__ import annotations

from typing import cast

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateTimeFilter
from unfold.dataclasses import ActionDialog
from unfold.decorators import action, display

from admin.base import BaseModelAdmin
from admin.displays import choice_label
from recommendation.context import current_vertical
from recommendation.enum import AttachMatch, EventKind, StrategyCode, Surface
from recommendation.models import (
    RecommendationCandidate,
    RecommendationEvent,
    RecommendationSlot,
)
from recommendation.presets import (
    reset_slot_to_preset,
    seed_recommendation_slots,
)
from tenant.models import StoreVertical

SURFACE_VARIANT: dict[str, str] = {
    Surface.PDP: "primary",
    Surface.CART: "success",
    Surface.OUT_OF_STOCK: "warning",
    Surface.EMPTY_CART: "info",
    Surface.ORDER_EMAIL: "info",
}

STRATEGY_VARIANT: dict[str, str] = {
    StrategyCode.CURATED: "success",
    StrategyCode.VARIANT_GROUP: "primary",
    StrategyCode.CATEGORY: "primary",
    StrategyCode.ATTRIBUTES: "info",
    StrategyCode.SEMANTIC: "info",
    StrategyCode.CO_PURCHASE: "warning",
    StrategyCode.CO_VIEW: "warning",
    StrategyCode.POPULAR: "danger",
}

EVENT_KIND_VARIANT: dict[str, str] = {
    EventKind.IMPRESSION: "info",
    EventKind.CLICK: "primary",
    EventKind.ATTACH: "success",
}

ATTACH_MATCH_VARIANT: dict[str, str] = {
    AttachMatch.IMPRESSION: "primary",
    AttachMatch.CART: "success",
    AttachMatch.USER: "info",
}


@admin.register(RecommendationSlot)
class RecommendationSlotAdmin(BaseModelAdmin):
    list_display = (
        "surface_label",
        "enabled",
        "chain_summary",
        "limit",
        "min_fill",
        "price_band_ratio",
        "updated_at",
    )
    list_filter = ["enabled", "surface"]
    search_fields = ("surface",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("surface", "enabled")}),
        (
            _("Strategy chain"),
            {
                "fields": ("strategy_chain", "weights"),
                "description": _(
                    "Ordered list of strategy codes; a strategy the plan "
                    "does not allow, or that has nothing to say for this "
                    "store, is skipped. Weights are optional and default "
                    "to 1.0."
                ),
            },
        ),
        (
            _("Limits"),
            {"fields": ("limit", "min_fill", "price_band_ratio")},
        ),
        (_("Timestamps"), {"fields": ("created_at", "updated_at")}),
    )

    surface_label = choice_label("surface", variants=SURFACE_VARIANT)

    @display(description=_("Chain"))
    def chain_summary(self, obj: RecommendationSlot) -> str:
        return " → ".join(obj.strategy_chain or [])

    # "Reset to preset" is the explicit way back after editing: seeding
    # never overwrites, so without this a merchant who broke a chain
    # would need a shell. Both actions read the store's own vertical
    # (``Tenant.vertical``, platform-set) — the same preset provisioning
    # used — and confirm through a dialog because they discard edits.
    actions_list = ["reset_all_to_preset"]
    actions_detail = ["reset_to_preset"]

    @action(
        description=_("Reset all slots to the store's preset"),
        icon="restart_alt",
        dialog=cast(
            "ActionDialog",
            {
                "title": _("Reset every slot to the preset?"),
                "description": _(
                    "Replaces the chain, weights, limits and price band "
                    "of every surface with the defaults for this store's "
                    "vertical. Your edits are lost."
                ),
            },
        ),
    )
    def reset_all_to_preset(self, request: HttpRequest, form) -> HttpResponse:
        vertical = current_vertical()
        seed_recommendation_slots(vertical)
        for slot in RecommendationSlot.objects.all():
            reset_slot_to_preset(slot, vertical)
        self.message_user(
            request,
            _("Every slot was reset to the %(vertical)s preset.")
            % {"vertical": StoreVertical(vertical).label},
            messages.SUCCESS,
        )
        return HttpResponse(
            headers={
                "HX-Redirect": reverse(
                    "admin:recommendation_recommendationslot_changelist"
                )
            }
        )

    @action(
        description=_("Reset to the store's preset"),
        icon="restart_alt",
        dialog=cast(
            "ActionDialog",
            {
                "title": _("Reset this slot to the preset?"),
                "description": _(
                    "Replaces this surface's chain, weights, limits and "
                    "price band with the defaults for this store's "
                    "vertical. Your edits are lost."
                ),
            },
        ),
    )
    def reset_to_preset(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        slot = RecommendationSlot.objects.get(pk=object_id)
        vertical = current_vertical()
        reset_slot_to_preset(slot, vertical)
        self.message_user(
            request,
            _("%(surface)s was reset to the %(vertical)s preset.")
            % {
                "surface": slot.get_surface_display(),
                "vertical": StoreVertical(vertical).label,
            },
            messages.SUCCESS,
        )
        return HttpResponse(
            headers={
                "HX-Redirect": reverse(
                    "admin:recommendation_recommendationslot_change",
                    args=[object_id],
                )
            }
        )


class _ReadOnlyAdmin(BaseModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(RecommendationCandidate)
class RecommendationCandidateAdmin(_ReadOnlyAdmin):
    list_display = (
        "product",
        "candidate",
        "strategy_label",
        "score",
        "relation_type",
        "computed_at",
    )
    list_filter = [
        "strategy",
        "relation_type",
        ("computed_at", RangeDateTimeFilter),
    ]
    # Product names live on the parler translation rows; the smoke test
    # executes every search field, so this must resolve.
    search_fields = ("product__translations__name",)
    list_select_related = ("product", "candidate")
    ordering = ("-computed_at",)

    strategy_label = choice_label("strategy", variants=STRATEGY_VARIANT)


@admin.register(RecommendationEvent)
class RecommendationEventAdmin(_ReadOnlyAdmin):
    list_display = (
        "created_at",
        "kind_label",
        "surface_label",
        "strategy_label",
        "product",
        "position",
        "matched_by_label",
        "order",
        "impression_id",
    )
    list_filter = [
        "kind",
        "surface",
        "strategy",
        "matched_by",
        ("created_at", RangeDateTimeFilter),
    ]
    search_fields = ("session_key", "cart_uuid", "impression_id")
    list_select_related = ("product", "seed", "order")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    kind_label = choice_label("kind", variants=EVENT_KIND_VARIANT)
    surface_label = choice_label("surface", variants=SURFACE_VARIANT)
    strategy_label = choice_label("strategy", variants=STRATEGY_VARIANT)
    matched_by_label = choice_label("matched_by", variants=ATTACH_MATCH_VARIANT)
