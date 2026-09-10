from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel
from product.enum.relation import RelationType
from recommendation.enum import EventKind, StrategyCode, Surface


class RecommendationSlot(TimeStampMixinModel):
    """Per-surface configuration: WHICH strategies run, in what order,
    with what weight, and how many results are worth showing.

    One row per ``Surface`` per tenant, seeded from a vertical preset
    at provisioning (``recommendation/presets.py``) and edited in admin.
    ``strategy_chain`` and ``weights`` are JSON lists/dicts validated
    by ``recommendation/schemas.py`` — the ``page_config`` idiom for
    ordered configuration — rather than a widget-heavy relation table,
    because a chain is edited a handful of times per store, ever.

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
        help_text=_(
            "Ordered list of strategy codes. Each fills the remaining "
            "slots in turn; a strategy that cannot answer for this "
            "store is skipped."
        ),
    )
    weights = models.JSONField(
        _("Weights"),
        default=dict,
        encoder=DjangoJSONEncoder,
        help_text=_(
            "Strategy code → weight in 0..1. Missing codes default to "
            "1.0. Updated nightly from attach rate on plans that learn."
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
        # Model-level so the admin's ModelForm surfaces a bad chain as a
        # field error instead of a 500 from save_model — and so a
        # shell or a preset seeding a bad row is caught the same way.
        from django.core.exceptions import ValidationError

        from recommendation.schemas import (
            validate_strategy_chain,
            validate_weights,
        )

        errors: dict[str, list[str]] = {}
        try:
            self.strategy_chain = validate_strategy_chain(self.strategy_chain)
        except ValidationError as exc:
            errors["strategy_chain"] = exc.messages
        else:
            try:
                self.weights = validate_weights(
                    self.weights, self.strategy_chain
                )
            except ValidationError as exc:
                errors["weights"] = exc.messages
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
    any catalogue size. Rebuilt per product on save via
    ``dispatch_on_commit`` and in full nightly for behavioural
    strategies; ``computed_at`` lets the engine spot a stale row and
    fall back to a live compute for that seed.
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
    response, the client echoes it on click, and order completion
    writes ``attach`` for any line whose product appeared under an
    impression in the same session. Events are recorded on every plan
    — one insert — and only the REPORTING is plan-gated, so the
    platform learns vertical presets from Free stores too.
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
    created_at = models.DateTimeField(_("Created at"), auto_now_add=True)

    class Meta(TypedModelMeta):
        verbose_name = _("Recommendation event")
        verbose_name_plural = _("Recommendation events")
        ordering = ["-created_at"]
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
