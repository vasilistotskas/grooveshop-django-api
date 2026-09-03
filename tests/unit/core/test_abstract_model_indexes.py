"""Every index an abstract parent declares must survive into the table.

Declaring ``indexes`` in a concrete ``Meta`` REPLACES the abstract
parents' list — Django does not merge them — so a model that adds one
index of its own silently drops every index its mixins declared unless
it splats them back in. Nothing fails, nothing warns; the queries just
go sequential.

``Order`` lost ``MetaDataModel``'s GIN pair exactly that way. It splatted
``TimeStampMixinModel.Meta.indexes`` and not ``MetaDataModel``'s, which
left the Viva webhook's ``metadata__contains`` lookup — run on every
delivery of an event Viva retries hourly — scanning the whole orders
table, while ``Product`` (which splats both) was indexed all along.

This walks the MRO rather than naming models, so a new mixin or a new
subclass is covered the day it is written.
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.db import models


# Models that drop a parent's indexes TODAY. Each is a real finding, not
# a decision: all eight lose ``TimeStampMixinModel``'s created_at /
# updated_at pair, and five of them (Contact, Feedback,
# CartPromotionCode, MetaCapiEventLog, and TenantArchive via
# destroyed_at) order every list query by a timestamp column that is
# therefore unindexed.
#
# They are recorded rather than fixed here because each belongs to a
# different app — tenant, contact, promotion, page_config, meta_capi —
# and each needs its own migration and its own "is this index worth its
# write cost on this table" call. This list must only ever shrink;
# anything new fails the test above.
_KNOWN_DRIFT: frozenset[str] = frozenset(
    {
        "tenant.Tenant",
        "tenant.UserTenantMembership",
        "tenant.TenantArchive",
        "contact.Contact",
        "contact.Feedback",
        "promotion.CartPromotionCode",
        "page_config.NavigationMenu",
        "meta_capi.MetaCapiEventLog",
    }
)


def _label(model: type[models.Model]) -> str:
    return f"{model._meta.app_label}.{model.__name__}"


def _concrete_models() -> list[type[models.Model]]:
    return [m for m in apps.get_models() if not m._meta.abstract]


def _abstract_parents(model: type[models.Model]):
    """Abstract model bases of ``model`` that declare their own indexes.

    Abstractness is read from ``base._meta.abstract``, NOT from
    ``base.Meta.abstract``: ``ModelBase`` sets the latter back to False
    once the class is built, so abstract models are indistinguishable
    from concrete ones by that attribute. Filtering on it silently
    matched nothing and made this whole module vacuous.
    """
    for base in model.__mro__[1:]:
        if not (isinstance(base, type) and issubclass(base, models.Model)):
            continue
        if not getattr(base, "_meta", None) or not base._meta.abstract:
            continue
        meta = getattr(base, "Meta", None)
        if meta is not None and getattr(meta, "indexes", None):
            yield base


def _index_signature(index: models.Index) -> tuple:
    """Compare on shape, not on name.

    ``%(class)s`` in an abstract index name is interpolated per model, so
    ``MetaDataModel``'s ``%(class)s_meta_ix`` is ``order_meta_ix`` on one
    table and ``product_meta_ix`` on another.
    """
    return (type(index).__name__, tuple(index.fields), tuple(index.opclasses))


def _dropped(model: type[models.Model]) -> list[str]:
    declared = {_index_signature(i) for i in model._meta.indexes}
    return [
        f"{parent.__name__}.{index.name}"
        for parent in _abstract_parents(model)
        for index in parent.Meta.indexes
        if _index_signature(index) not in declared
    ]


@pytest.mark.parametrize("model", _concrete_models(), ids=_label)
def test_concrete_model_keeps_its_abstract_parents_indexes(model):
    if _label(model) in _KNOWN_DRIFT:
        pytest.skip("known drift, see _KNOWN_DRIFT")

    assert not _dropped(model), (
        f"{_label(model)}.Meta.indexes drops {_dropped(model)}. Defining "
        f"`indexes` REPLACES the abstract parents' list rather than "
        f"extending it — splat the parent's list back in, e.g. "
        f"`*TimeStampMixinModel.Meta.indexes`."
    )


@pytest.mark.parametrize("label", sorted(_KNOWN_DRIFT))
def test_known_drift_list_only_shrinks(label):
    """A model that has been fixed must be removed from the allowlist.

    Without this, the allowlist would keep silencing the check long after
    the reason for it is gone.
    """
    model = apps.get_model(label)
    assert _dropped(model), (
        f"{label} no longer drops a parent index — remove it from "
        f"_KNOWN_DRIFT so the check guards it from now on."
    )
