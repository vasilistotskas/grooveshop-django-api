"""Refusing to delete public reference data a store still uses.

``Country`` and ``Region`` live in the public schema and every store
reads them (``tenant.role_scopes.REFERENCE_DATA_APP_LABELS``). The rows
that point at them do not: an order, a saved address, a shipping rate or
a pay way is in the STORE's schema. Django's deletion collector, run from
the public schema, cannot see those tables, so on its own it neither
refuses nor warns: a store's order would be left naming a country that is
gone, or the delete fails on a table that is not there.

This module asks every store's schema instead. It finds the references
from the models' own reverse relations (hidden ones included, which is
where ``ShippingRate.country`` and ``PayWay.country`` are, both
``related_name="+"``), so a new model that points at a country is covered
the day it is added.

Used by ``tenant.deletion.PublicReferenceDataDeletionMixin`` (the
confirmation page, with the stores named) and by the ``pre_delete``
receivers in ``tenant.signals`` (every other delete, such as the shell or
a management command).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.db.models import Model
from django.db.models.fields.reverse_related import ForeignObjectRel
from django.utils.translation import gettext
from django_tenants.utils import (
    get_public_schema_name,
    schema_context,
    schema_exists,
)
from simple_history.models import HistoricalChanges

from tenant.app_labels import shared_only_app_labels


@dataclass(frozen=True)
class StoreReferences:
    """What one store still holds that points at the rows being deleted."""

    store: str
    #: Verbose plural model name -> how many rows.
    counts: dict[str, int]

    def __str__(self) -> str:
        rows = ", ".join(f"{n} {name}" for name, n in self.counts.items())
        return gettext("%(store)s: used by %(rows)s") % {
            "store": self.store,
            "rows": rows,
        }


def _reverse_relations(model: type[Model]) -> list[ForeignObjectRel]:
    """Relations that point AT ``model`` from a model a store owns.

    ``include_hidden`` is what finds the ``related_name="+"`` ones. A
    model in a shared-only app (the translations of a country) is deleted
    with it and holds nothing a store owns. A simple-history table only
    records what a row once said (its foreign key has no constraint), and
    it never goes away, so counting it would block the delete forever.
    """
    shared = set(shared_only_app_labels())
    return [
        relation
        for relation in model._meta.get_fields(include_hidden=True)
        if isinstance(relation, ForeignObjectRel)
        and (relation.one_to_many or relation.one_to_one)
        and relation.related_model._meta.app_label not in shared
        and not issubclass(relation.related_model, HistoricalChanges)
    ]


def _targets(instances: Iterable[Model]) -> dict[type[Model], set[Any]]:
    """The primary keys being deleted, by model.

    A country takes its regions with it (``Region.country`` cascades), so
    a store using one of them is using the country.
    """
    from country.models import Country
    from region.models import Region

    targets: dict[type[Model], set[Any]] = {}
    for instance in instances:
        targets.setdefault(type(instance), set()).add(instance.pk)
    countries = targets.get(Country)
    if countries:
        targets.setdefault(Region, set()).update(
            Region.objects.filter(country__in=countries).values_list(
                "pk", flat=True
            )
        )
    return targets


def references_to(instances: Iterable[Model]) -> list[StoreReferences]:
    """Every store that still uses any of ``instances``, with counts.

    Reads each store's schema, so it is correct from any schema context.
    A store whose schema no longer exists (offboarded) cannot hold a
    reference and is skipped.
    """
    from tenant.models import Tenant

    targets = _targets(instances)
    found: list[StoreReferences] = []
    stores = Tenant.objects.exclude(
        schema_name=get_public_schema_name()
    ).order_by("name")
    for store in stores:
        if not schema_exists(store.schema_name):
            continue
        counts: Counter[str] = Counter()
        with schema_context(store.schema_name):
            for model, pks in targets.items():
                for relation in _reverse_relations(model):
                    owner = relation.related_model
                    n = owner._base_manager.filter(
                        **{f"{relation.field.name}__in": pks}
                    ).count()
                    if n:
                        counts[str(owner._meta.verbose_name_plural)] += n
        if counts:
            found.append(StoreReferences(store=store.name, counts=dict(counts)))
    return found
