from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.json_schema import JSONSchemaValidator
from core.models import TimeStampMixinModel
from product.enum.relation import RelationType
from recommendation.enum import (
    SEEDLESS_SURFACES,
    AttachMatch,
    EventKind,
    StrategyCode,
    Surface,
)


class RecommendationSlot(TimeStampMixinModel):
    """Per-surface configuration: WHICH strategies run, in what order,
    with what weight, and how many results are worth showing.

    One row per ``Surface`` per tenant, seeded from a vertical preset
    at provisioning (``recommendation/presets.py``) and edited in admin.
    With ``learn_weights`` on, ``weights`` is rewritten nightly by
    ``recommendation.learning`` from the surface's attach rates.
    ``strategy_chain`` and ``weights`` are JSON validated against
    ``recommendation/json_schemas.py`` rather than a widget-heavy
    relation table, because a chain is edited a handful of times per
    store, ever.

    ``min_fill`` is the guard against a broken-looking strip: below it
    the engine returns nothing and the storefront renders nothing.
    """

    surface = models.CharField(
        _("Surface"),
        max_length=20,
        choices=Surface.choices,
        unique=True,
    )
    strategy_chain = models.JSONField(
        _("Strategy chain"),
        default=list,
        encoder=DjangoJSONEncoder,
        validators=[
            JSONSchemaValidator("recommendation.json_schemas.strategy_chain")
        ],
        help_text=_(
            "Ordered list of strategy codes. Each fills the remaining "
            "slots in turn; a strategy that cannot answer for this "
            "store is skipped."
        ),
    )
    weights = models.JSONField(
        _("Weights"),
        default=dict,
        # An empty mapping is the normal state ("unweighted") — without
        # ``blank`` the admin form and ``full_clean`` refuse it.
        blank=True,
        encoder=DjangoJSONEncoder,
        validators=[JSONSchemaValidator("recommendation.json_schemas.weights")],
        help_text=_(
            "Strategy code → weight in 0..1. Missing codes default to "
            '1.0. With "Learn weights" on, replaced nightly from each '
            "strategy's attach rate on this surface."
        ),
    )
    learn_weights = models.BooleanField(
        _("Learn weights"),
        default=True,
        db_default=True,
        help_text=_(
            "Every night, re-weight the strategies by how often the "
            "products each one suggested here were then bought. A "
            "strategy needs enough recent impressions before its weight "
            "moves. Turn off to keep weights you set by hand."
        ),
    )
    limit = models.PositiveSmallIntegerField(_("Limit"), default=4)
    min_fill = models.PositiveSmallIntegerField(
        _("Minimum fill"),
        default=2,
        help_text=_(
            "Show nothing rather than fewer than this many suggestions."
        ),
    )
    price_band_ratio = models.DecimalField(
        _("Price band ratio"),
        max_digits=4,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_(
            "Drop candidates whose price is more than this many times "
            "the seed's, or less than 1/this. Empty disables the band."
        ),
    )
    enabled = models.BooleanField(_("Enabled"), default=True)

    class Meta(TypedModelMeta):
        verbose_name = _("Recommendation slot")
        verbose_name_plural = _("Recommendation slots")
        ordering = ["surface"]
        indexes = [*TimeStampMixinModel.Meta.indexes]

    def __str__(self) -> str:
        return self.get_surface_display()

    def clean(self) -> None:
        """The rules the field schemas cannot express: every weighted
        code is in the chain, ``min_fill`` fits in ``limit``, and a
        seedless surface chains only strategies that can answer without
        a seed. Weights are stored as floats."""
        from django.core.exceptions import ValidationError

        from recommendation.strategies import get_strategy

        errors: dict[str, list[str]] = {}
        if self.surface in SEEDLESS_SURFACES and isinstance(
            self.strategy_chain, list
        ):
            # Unknown codes are the field validator's error, not this one.
            unable = [
                str(get_strategy(code).label)
                for code in self.strategy_chain
                if code in StrategyCode.values
                and not get_strategy(code).seedless
            ]
            if unable:
                errors["strategy_chain"] = [
                    str(
                        _(
                            "This surface has no product to start from, so "
                            "these strategies have nothing to suggest "
                            "here: %(names)s"
                        )
                        % {"names": ", ".join(unable)}
                    )
                ]
        if isinstance(self.weights, dict) and isinstance(
            self.strategy_chain, list
        ):
            stray = sorted(set(self.weights) - set(self.strategy_chain))
            if stray:
                errors["weights"] = [
                    str(
                        _("Weighted strategies not in the chain: %(codes)s")
                        % {"codes": ", ".join(stray)}
                    )
                ]
            elif all(
                isinstance(weight, int | float) and not isinstance(weight, bool)
                for weight in self.weights.values()
            ):
                self.weights = {
                    code: float(weight) for code, weight in self.weights.items()
                }
        if self.min_fill > self.limit:
            errors["min_fill"] = [
                str(_("Minimum fill cannot exceed the limit."))
            ]
        if errors:
            raise ValidationError(errors)


class RecommendationCandidate(models.Model):
    """The offline half of two-stage retrieval: top-K candidates per
    (product, strategy), written by Celery, read by the request path.

    This table is the seam that keeps the read path constant-cost at
    any catalogue size. Rows exist only for the strategies with
    ``precompute`` set (``attributes``, ``co_purchase``): rebuilt per
    product on save via ``dispatch_on_commit`` and for the whole
    catalogue nightly (``tasks.recompute_all_candidates``).
    """

    product = models.ForeignKey(
        "product.Product",
        on_delete=models.CASCADE,
        related_name="recommendation_candidates",
        verbose_name=_("Product"),
    )
    candidate = models.ForeignKey(
        "product.Product",
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name=_("Candidate"),
    )
    strategy = models.CharField(
        _("Strategy"), max_length=20, choices=StrategyCode.choices
    )
    score = models.DecimalField(_("Score"), max_digits=6, decimal_places=4)
    # Empty for every strategy but ``curated``; the engine reads it back
    # as ``None`` so the API's ``relationType`` stays ``null``, not "".
    relation_type = models.CharField(
        _("Relation type"),
        max_length=20,
        choices=RelationType.choices,
        blank=True,
        default="",
    )
    computed_at = models.DateTimeField(_("Computed at"), auto_now=True)

    class Meta(TypedModelMeta):
        verbose_name = _("Recommendation candidate")
        verbose_name_plural = _("Recommendation candidates")
        constraints = [
            models.UniqueConstraint(
                fields=["product", "candidate", "strategy"],
                name="recommendation_candidate_unique",
            ),
            models.CheckConstraint(
                condition=~Q(product=F("candidate")),
                name="recommendation_candidate_not_self",
            ),
        ]
        indexes = [
            # The read path: all candidates for a seed, best first.
            BTreeIndex(
                fields=["product", "strategy", "-score"],
                name="rec_candidate_read_ix",
            ),
            BTreeIndex(fields=["computed_at"], name="rec_candidate_at_ix"),
        ]

    def __str__(self) -> str:
        return f"{self.product_id} -> {self.candidate_id} ({self.strategy})"


class RecommendationEvent(models.Model):
    """The feedback loop: what was shown, what was clicked, what was
    bought. Follows ``search.SearchClick``.

    ``impression_id`` ties the three together: the API mints one per
    response, the client echoes it on impression and click, and order
    creation writes ``attach`` for any line whose product appeared under
    an impression shown to the same cart or the same signed-in customer
    inside the attribution windows (``settings.RECOMMENDATION_ATTACH_*``,
    ``events.record_attach_events``). Events are recorded on every plan
    — one insert — and only the REPORTING is plan-gated, so the
    platform learns vertical presets from Free stores too.

    ``cart_uuid`` is the identity that spans the journey: the cart row
    itself is a per-customer singleton whose lines are cleared at
    checkout, so an ``attach`` is matched on the UUID the order snapshots
    (``metadata.cart_snapshot.cart_uuid``), never on the row.
    """

    surface = models.CharField(
        _("Surface"), max_length=20, choices=Surface.choices
    )
    strategy = models.CharField(
        _("Strategy"), max_length=20, choices=StrategyCode.choices
    )
    kind = models.CharField(_("Kind"), max_length=12, choices=EventKind.choices)
    seed = models.ForeignKey(
        "product.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Seed product"),
    )
    product = models.ForeignKey(
        "product.Product",
        on_delete=models.CASCADE,
        related_name="recommendation_events",
        verbose_name=_("Product"),
    )
    position = models.PositiveSmallIntegerField(
        _("Position"), null=True, blank=True
    )
    impression_id = models.UUIDField(_("Impression"), db_index=True)
    session_key = models.CharField(
        _("Session key"), max_length=40, blank=True, db_index=True
    )
    cart_uuid = models.UUIDField(
        _("Cart"), null=True, blank=True, db_index=True
    )
    # ORM-only: the user table is per-schema too, but the platform
    # rule for FKs to it is no database constraint (see
    # ``Product.changed_by``).
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_constraint=False,
        related_name="+",
        verbose_name=_("User"),
    )
    # ``attach`` rows only: the order the line landed in, and which
    # window tied it back to the impression.
    order = models.ForeignKey(
        "order.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recommendation_events",
        verbose_name=_("Order"),
    )
    matched_by = models.CharField(
        _("Matched by"),
        max_length=12,
        choices=AttachMatch.choices,
        blank=True,
        default="",
    )
    created_at = models.DateTimeField(_("Created at"), auto_now_add=True)

    class Meta(TypedModelMeta):
        verbose_name = _("Recommendation event")
        verbose_name_plural = _("Recommendation events")
        ordering = ["-created_at"]
        constraints = [
            # One attach per (order, product, impression): the task that
            # writes them retries, and a retry must not double-count.
            models.UniqueConstraint(
                fields=["order", "product", "impression_id"],
                condition=Q(kind="attach"),
                name="rec_event_attach_unique",
            ),
        ]
        indexes = [
            BTreeIndex(fields=["created_at"], name="rec_event_created_ix"),
            BTreeIndex(
                fields=["kind", "created_at"], name="rec_event_kind_at_ix"
            ),
            BTreeIndex(
                fields=["surface", "strategy", "kind"],
                name="rec_event_surface_strategy_ix",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.product_id} @ {self.surface}"
