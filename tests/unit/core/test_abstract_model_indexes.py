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

Two escape hatches, kept apart on purpose: ``_DELIBERATE`` for an
omission someone decided on and wrote down, ``_KNOWN_DRIFT`` for one
nobody has looked at yet. A third test fails if either entry stops being
necessary, so neither list can outlive its reason.

This walks the MRO rather than naming models, so a new mixin or a new
subclass is covered the day it is written.
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.db import models

# Indexes a model drops ON PURPOSE, with the reason. Distinct from
# _KNOWN_DRIFT below: nothing here is waiting to be fixed.
_DELIBERATE: dict[str, str] = {
    "contact.Feedback": (
        "Takes created_at — the list orders by it and `date_hierarchy` "
        "range-scans it — but not updated_at, which the admin displays "
        "and never sorts or filters."
    ),
    "promotion.CartPromotionCode": (
        "Takes created_at for `ordering` and `date_hierarchy`; nothing "
        "reads updated_at."
    ),
    "meta_capi.MetaCapiEventLog": (
        "Carries its own DESCENDING `-created_at` index to match the "
        "ordering, rather than the parent's ascending one, and none on "
        "updated_at: this is an append-only audit log with a row per "
        "dispatch attempt, so every needless index is paid on write."
    ),
    "tenant.Tenant": (
        "Neither timestamp is ordered or filtered on — the console lists "
        "tenants by name and status — and tenants number in dozens."
    ),
    "tenant.UserTenantMembership": (
        "Looked up by (tenant, user), both already indexed. Neither "
        "timestamp is queried."
    ),
    "tenant.TenantArchive": (
        "Orders by destroyed_at, which has its own index, and sweeps by "
        "retention date via tenant_arch_ret_ix. created_at/updated_at are "
        "never read."
    ),
    "page_config.NavigationMenu": (
        "Ordered by `slot`; a handful of rows per tenant and neither "
        "timestamp is queried."
    ),
    "order.Order": (
        "Takes MetaDataModel's `metadata` GIN index explicitly but not "
        "its `private_metadata` one: no query reads that column on an "
        "order (MetaDataFilterMixin, which exposes the only lookup, is "
        "not among OrderFilter's bases), and a GIN index costs a "
        "pending-list write on every order INSERT and UPDATE."
    ),
}

# Empty, and it should stay that way: a model that drops a parent index
# without a decision behind it belongs in the assertion, not here. The
# eight that used to sit in this set were worked through one at a time —
# four earned an index and got one, four did not and say why above.
_KNOWN_DRIFT: frozenset[str] = frozenset()


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
    label = _label(model)
    if label in _DELIBERATE:
        pytest.skip(_DELIBERATE[label])
    if label in _KNOWN_DRIFT:
        pytest.skip("known drift, see _KNOWN_DRIFT")

    assert not _dropped(model), (
        f"{_label(model)}.Meta.indexes drops {_dropped(model)}. Defining "
        f"`indexes` REPLACES the abstract parents' list rather than "
        f"extending it — splat the parent's list back in, e.g. "
        f"`*TimeStampMixinModel.Meta.indexes`."
    )


@pytest.mark.parametrize("label", sorted(_KNOWN_DRIFT | frozenset(_DELIBERATE)))
def test_every_exception_is_still_needed(label):
    """An exempted model that no longer drops anything must be un-exempted.

    Without this, either list would keep silencing the check long after
    the reason for it is gone.
    """
    model = apps.get_model(label)
    assert _dropped(model), (
        f"{label} no longer drops a parent index — remove it from "
        f"_KNOWN_DRIFT / _DELIBERATE so the check guards it from now on."
    )


def test_order_still_has_the_index_the_viva_webhook_needs():
    """The half of MetaDataModel's pair that Order DOES take.

    ``_DELIBERATE`` exempts Order from the parent-index check, so without
    this the exemption would also hide the loss of ``order_meta_ix``
    itself — the thing the whole exercise was about.
    """
    from django.contrib.postgres.indexes import GinIndex

    order = apps.get_model("order.Order")
    metadata_indexes = [
        i
        for i in order._meta.indexes
        if isinstance(i, GinIndex) and i.fields == ["metadata"]
    ]
    assert metadata_indexes, (
        "Order lost its GIN index on `metadata`. The Viva webhook "
        "resolves every delivery through metadata__contains; without it "
        "that is a sequential scan of the whole orders table."
    )
