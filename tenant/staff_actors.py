"""Staff members named on rows that live in a store's schema.

Every admin user of a store is a PLATFORM-PUBLIC identity
(``PlatformStaffBackend``), while the rows they act on (a product, a
shipment, a stock movement) live in the store's schema, where
``user_useraccount`` holds the store's SHOPPERS. A foreign key from such a
row to ``UserAccount`` therefore points the staff id at the wrong table:
no constraint can be declared across schemas, and reading the relation
returns whichever shopper happens to share the id (verified in
``tests_mt/test_staff_actor_resolution.py``).

So these columns are plain ids with no relation at all. Reading one goes
through :class:`StaffActor`, which looks the id up in ``public``; there is
no ORM join to get wrong, because there is no join. A column that must name
a shopper as well (``StockLog``) keeps a real foreign key beside its
staff id, and the two never share a column.

``admin.log_actors`` does the same for ``django_admin_log``, whose columns
belong to Django.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.contrib.auth import get_user_model
from django_tenants.utils import get_public_schema_name, schema_context

from tenant.auth_backends import PLATFORM_IDENTITY_ATTR


class UnknownActor:
    """Stand-in for an actor that no longer exists in the public schema.

    Caching ``None`` would NOT do: ``ForwardManyToOneDescriptor`` treats
    a cached ``None`` alongside a non-null ``user_id`` as a cache miss
    and falls back to a database lookup — against the TENANT's user
    table, which is precisely the misattribution this module exists to
    prevent. A non-``None`` sentinel is what actually stops that query.

    Deliberately not a ``UserAccount`` instance: it must be impossible
    to mistake for a real account, and fabricating an identity for an
    actor we cannot identify would be worse than admitting we cannot.
    It renders as empty in ``object_history.html`` while keeping the pk
    available for anyone debugging the row.
    """

    __slots__ = ("pk",)

    def __init__(self, pk: object) -> None:
        self.pk = pk

    def get_username(self) -> str:
        return ""

    def get_full_name(self) -> str:
        return ""

    def __str__(self) -> str:
        return ""

    def __repr__(self) -> str:
        return f"<UnknownActor pk={self.pk!r}>"


class StaffRef:
    """A staff id standing in for the user, for code that only needs the pk
    (simple_history's setter): no lookup, and stamped as a platform identity
    so :func:`staff_id` accepts it."""

    __slots__ = ("pk",)

    def __init__(self, pk: int) -> None:
        self.pk = pk


setattr(StaffRef, PLATFORM_IDENTITY_ATTR, True)


def staff_id(user: Any) -> int | None:
    """The id to store for *user*, or None when they are not staff.

    Only an identity loaded from the public schema by the staff backend
    (the provenance stamp) is stored. A shopper acting through the API, the
    system, or nobody is None: a shopper's id read as a staff id would name
    an unrelated staff member.
    """
    if user is None or not getattr(user, PLATFORM_IDENTITY_ATTR, False):
        return None
    return user.pk


def resolve_staff(ids: Iterable[int | None]) -> dict[int, Any]:
    """The staff accounts behind *ids*, read from the public schema."""
    wanted = {pk for pk in ids if pk is not None}
    if not wanted:
        return {}
    with schema_context(get_public_schema_name()):
        return {
            user.pk: user
            for user in get_user_model()._default_manager.filter(pk__in=wanted)
        }


def _cached(obj: Any, name: str, pk: int) -> Any | None:
    """The actor bound for ``name``, if it is still the one ``pk`` names."""
    actor = obj._state.fields_cache.get(name)
    return actor if actor is not None and actor.pk == pk else None


def bind_staff_actors(objs: Iterable[Any], *names: str) -> None:
    """Resolve the named actors of every object in one public query.

    ``prefetch_related(name)`` does the same through Django's own
    machinery (:meth:`StaffActor.get_prefetch_querysets`); this is for a
    single object and for code that already holds the rows.
    """
    objs = list(objs)
    ids = {getattr(obj, f"{name}_id") for obj in objs for name in names}
    users = resolve_staff(ids)
    for obj in objs:
        for name in names:
            pk = getattr(obj, f"{name}_id")
            if pk is not None:
                obj._state.fields_cache[name] = users.get(pk) or UnknownActor(
                    pk
                )


class StaffActor(property):
    """``obj.<name>``: the staff member whose id is in ``<name>_id``.

    ``None`` when the column is empty, the account when it resolves in
    ``public``, and an :class:`UnknownActor` (renders empty, keeps the pk)
    for an account that no longer exists. Assigning a user stores their id
    if they are staff (:func:`staff_id`). A ``property`` so that
    ``Model(<name>=user)`` and ``objects.create(<name>=user)`` work.

    A page of rows resolves in one query with ``prefetch_related(<name>)``;
    there is deliberately no ``select_related``, because there is no join
    to the store's user table to get wrong.
    """

    def __init__(self) -> None:
        super().__init__(self._read, self._write)
        self.name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def _read(self, obj: Any) -> Any:
        pk = getattr(obj, f"{self.name}_id")
        if pk is None:
            return None
        actor = _cached(obj, self.name, pk)
        if actor is None:
            bind_staff_actors([obj], self.name)
            actor = obj._state.fields_cache[self.name]
        return actor

    def _write(self, obj: Any, value: Any) -> None:
        setattr(obj, f"{self.name}_id", staff_id(value))

    # Django's prefetch protocol (``prefetch_one_level``). The "queryset" is
    # the already-resolved list: Django only iterates it.
    def get_prefetch_querysets(self, instances, querysets=None):
        ids = {
            pk
            for obj in instances
            if (pk := getattr(obj, f"{self.name}_id")) is not None
        }
        users = resolve_staff(ids)
        # A deleted account still gets a stand-in, or each of its rows would
        # read as a cache miss and look the id up again on its own.
        actors = [users.get(pk) or UnknownActor(pk) for pk in ids]
        return (
            actors,
            lambda user: user.pk,
            lambda obj: getattr(obj, f"{self.name}_id"),
            True,
            self.name,
            False,
        )

    def is_cached(self, instance: Any) -> bool:
        return self.name in instance._state.fields_cache


def history_user_getter(historical_instance: Any) -> Any:
    """simple_history's ``history_user`` for a column holding a staff id."""
    pk = historical_instance.history_user_id
    if pk is None:
        return None
    actor = _cached(historical_instance, "history_user", pk)
    if actor is None:
        actor = resolve_staff([pk]).get(pk) or UnknownActor(pk)
        historical_instance._state.fields_cache["history_user"] = actor
    return actor


def history_user_setter(historical_instance: Any, user: Any) -> None:
    historical_instance.history_user_id = staff_id(user)
