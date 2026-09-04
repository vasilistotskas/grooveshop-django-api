"""A nullable string field must not still be MINTING NULLs.

Django's own field docs advise against ``null=True`` on string-based
fields: "no data" then has two spellings, NULL and "", and every reader
has to know both. Ruff's ``DJ001`` flags them, and the fix is ``NOT NULL``
with ``default=""``.

That fix is a **two-release** change here. Under the Argo CD PreSync hook
migrations land before the new image, so the ALTER that adds the
constraint would run while pods that still write NULL are serving: the
first release has to stop producing NULL and backfill, and only the
second may add the constraint. This module guards the first half — the
half a lint rule cannot see.

The lever is ``Field.get_default()``. For a string field with
``null=True`` and no ``default``, it returns **None**, so every row
written without an explicit value gets NULL; with ``default=""`` it
returns "". That is the whole mechanism behind seven NULL profile URLs on
every account ever created, and it is what this module pins.

It also catches what ``DJ001`` structurally cannot: that rule matches
Django's own field classes, so a third-party subclass slips past it.
``UserAccount.phone`` — a ``PhoneNumberField``, i.e. a ``CharField`` —
was found exactly this way.

When the constraint lands, ``DJ001`` becomes enforceable on its own and
this module goes with the exemption it guards.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.apps import apps
from django.db import models
from djmoney.models.fields import CurrencyField

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Parler translated fields, deferred WITH a reason rather than silently
# skipped. They have the same two-spellings defect, but fixing them is
# not the same job: every reader reaches them through a LEFT JOIN, where
# ``translations__name__isnull=True`` means "no translation ROW" at least
# as often as it means "NULL column". Collapsing the column would leave
# those filters looking right and meaning something narrower — and
# ``ProductReviewQuerySet.with_comments()`` (``isnull=False``) would
# start counting reviews whose comment is "". Each of the ~15 filter
# sites needs deciding on its own, which is a semantic pass, not this
# mechanical one.
_DEFERRED_TRANSLATED_FIELDS = frozenset(
    {
        "blog.BlogAuthorTranslation.bio",
        "blog.BlogCategoryTranslation.description",
        "blog.BlogCategoryTranslation.name",
        "blog.BlogCommentTranslation.content",
        "blog.BlogPostTranslation.body",
        "blog.BlogTagTranslation.name",
        "country.CountryTranslation.name",
        "page_config.ContentPageTranslation.body",
        "pay_way.PayWayTranslation.description",
        "pay_way.PayWayTranslation.instructions",
        "pay_way.PayWayTranslation.name",
        "product.ProductCategoryImageTranslation.alt_text",
        "product.ProductCategoryImageTranslation.title",
        "product.ProductCategoryTranslation.description",
        "product.ProductCategoryTranslation.name",
        "product.ProductImageTranslation.title",
        "product.ProductReviewTranslation.comment",
        "product.ProductTranslation.description",
        "product.ProductVariantGroupTranslation.name",
        "region.RegionTranslation.name",
        "tag.TagTranslation.label",
    }
)

_STRING_FIELDS = (models.CharField, models.TextField)


def _is_first_party(model: type[models.Model]) -> bool:
    """Ours, not a dependency's.

    The virtualenv lives INSIDE the repo, so "under the repo root" alone
    admits every installed package — allauth, dj-stripe and
    django-celery-beat all ship nullable string fields we cannot change.
    """
    try:
        path = Path(model._meta.app_config.path).resolve()
    except AttributeError, TypeError:
        return False
    if "site-packages" in path.parts or ".venv" in path.parts:
        return False
    return _REPO_ROOT in path.parents or path == _REPO_ROOT


def _is_exempt(model: type[models.Model], field: models.Field) -> bool:
    """Structural exemptions — rules, not per-model judgements."""
    # Django's own documented exception: with UNIQUE, NULL is the only
    # value that can repeat, so it is the only way to say "absent" on
    # more than one row. BoxNowShipment.parcel_id and friends.
    if field.unique:
        return True
    # django-money generates a <name>_currency companion per MoneyField;
    # its nullability follows the money field, and djmoney owns it.
    if isinstance(field, CurrencyField):
        return True
    # A FileField stores a path and has no "empty" value distinct from
    # NULL that the storage layer would treat differently.
    if isinstance(field, models.FileField):
        return True
    # simple-history mirrors its source model field for field, including
    # nullability, plus a history_change_reason of its own. Fixing the
    # source model is what moves these.
    return getattr(model, "instance_type", None) is not None


def _nullable_string_fields() -> list[tuple[str, models.Field]]:
    found = []
    for model in apps.get_models():
        if model._meta.abstract or not _is_first_party(model):
            continue
        for field in model._meta.get_fields():
            if not isinstance(field, _STRING_FIELDS) or not field.null:
                continue
            if _is_exempt(model, field):
                continue
            found.append((f"{model._meta.label}.{field.name}", field))
    return sorted(found, key=lambda pair: pair[0])


_NULLABLE = _nullable_string_fields()
_CHECKED = [
    pair for pair in _NULLABLE if pair[0] not in _DEFERRED_TRANSLATED_FIELDS
]


@pytest.mark.parametrize(
    ("label", "field"), _CHECKED, ids=[label for label, _ in _CHECKED]
)
def test_nullable_string_field_defaults_to_empty_string(label, field):
    assert field.get_default() == "", (
        f"{label} allows NULL and has no empty-string default, so "
        f"Field.get_default() returns {field.get_default()!r} and every "
        f"row written without an explicit value adds another NULL to a "
        f"column we are trying to empty. Give it a default of '' — or "
        f"drop null=True outright, which is the destination."
    )


@pytest.mark.parametrize("label", sorted(_DEFERRED_TRANSLATED_FIELDS))
def test_every_deferred_field_is_still_deferred(label):
    """A field that got fixed must leave the deferred list.

    Without this, the list would keep excusing fields long after the
    reason for excusing them is gone — and it would go on naming a field
    someone deleted, hiding a stale entry.
    """
    by_label = dict(_NULLABLE)
    assert label in by_label, (
        f"{label} is no longer a nullable string field without a default "
        f"— remove it from _DEFERRED_TRANSLATED_FIELDS so the check "
        f"guards it from now on."
    )
    assert by_label[label].get_default() != "", (
        f"{label} now has an empty-string default — remove it from "
        f"_DEFERRED_TRANSLATED_FIELDS."
    )


def test_the_check_has_something_to_check():
    """Guard against the parametrize list going silently empty.

    An earlier invariant test in this repo filtered on ``Meta.abstract``,
    which ``ModelBase`` resets to False once the class is built. It
    matched nothing and passed identically with and without the bug it
    was written for.
    """
    assert _CHECKED, (
        "No checkable nullable string fields found. Either every one now "
        "has a default — in which case the NOT NULL release is due and "
        "this module goes with it — or the model walk stopped matching."
    )
