"""Anonymise a restored production database, in place, for staging.

``anonymise_database()`` runs over the public schema and every tenant
schema inside ONE transaction:

1. build the set of identities that must keep working (platform staff,
   each store's shared demo logins);
2. apply every ``Scrub`` and ``Delete`` of ``rules.REGISTRY`` to the
   schemas its model lives in;
3. read the whole database back for any original value that survived
   (``scan.py``) and roll everything back if one did.

Only ``QuerySet.update``, ``bulk_update`` and ``delete`` are used, never
``save()``: no signal fires, so nothing is indexed, mailed, queued or
cache-invalidated by the rewrite, and ``updated_at`` keeps its value.

The command refuses anywhere but ``SYSTEM_ENV=staging`` (and this module
does again, so a caller cannot skip the command's check).
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Model, ProtectedError, Q
from django_tenants.utils import get_public_schema_name, schema_context

from devtools.anonymise import fakes, redaction, rules, scan
from devtools.anonymise.rules import F, Kind
from tenant.app_labels import shared_only_app_labels, tenant_only_app_labels

logger = logging.getLogger(__name__)

STAGING_ENV = "staging"
_BATCH = 1000
# Schemas Postgres or the operator own; not store data.
_SYSTEM_SCHEMAS = frozenset(
    {"information_schema", "metric_helpers", "user_management"}
)
_DEMO_SETTINGS = (
    "DEMO_ACCOUNT_ENABLED",
    "DEMO_ACCOUNT_EMAIL",
    "DEMO_ACCOUNT_B2B_EMAIL",
)


class NotStaging(RuntimeError):
    """The anonymiser was asked to run outside staging."""


class ResidueFound(RuntimeError):
    """An original value survived the rewrite."""


class UnknownSchema(RuntimeError):
    """The database holds a schema no tenant row accounts for."""


@dataclass
class Report:
    scrubbed: Counter[str] = field(default_factory=Counter)
    deleted: Counter[str] = field(default_factory=Counter)
    cleared: Counter[str] = field(default_factory=Counter)
    kept_users: dict[str, int] = field(default_factory=dict)

    def lines(self) -> Iterator[str]:
        for label, count in sorted(self.scrubbed.items()):
            yield f"scrubbed {count:>7}  {label}"
        for label, count in sorted(self.cleared.items()):
            yield f"cleared  {count:>7}  {label} credentials"
        for label, count in sorted(self.deleted.items()):
            yield f"deleted  {count:>7}  {label}"
        for schema, count in sorted(self.kept_users.items()):
            yield f"kept     {count:>7}  accounts in {schema}"


@dataclass
class Subject:
    """Whose data a row is, resolved to a fake person and place."""

    person: fakes.Person
    place: fakes.Place


@dataclass
class _Context:
    """Per-run state shared by every schema."""

    canaries: scan.Canaries = field(default_factory=scan.Canaries)
    emitted: set[str] = field(default_factory=set)
    allowed: set[str] = field(default_factory=set)
    report: Report = field(default_factory=Report)


@dataclass
class _Schema:
    """Per-schema state, read BEFORE any row of it is rewritten."""

    name: str
    kept: frozenset[int]
    user_emails: dict[int, str]
    order_users: dict[int, int | None]


def _require_staging() -> None:
    if settings.SYSTEM_ENV != STAGING_ENV:
        raise NotStaging(
            f"SYSTEM_ENV is {settings.SYSTEM_ENV!r}: refusing to anonymise "
            f"a database outside {STAGING_ENV!r}"
        )


def _present(value: Any) -> bool:
    """Is there anything to replace? ``None`` and empty containers: no."""
    if value is None:
        return False
    if isinstance(value, str | bytes | list | tuple | dict):
        return bool(value)
    return True


def _in_schema(model: type[Model], schema: str, public: str) -> bool:
    app = model._meta.app_label
    if schema == public:
        return app not in tenant_only_app_labels()
    return app not in shared_only_app_labels()


def _demo_emails() -> set[str]:
    """The shared demo logins of the CURRENT schema, read from the table.

    Not through ``Setting.get``: that reads a cache keyed by schema, which
    on staging holds the previous database's values.
    """
    from extra_settings.models import Setting

    values = {
        s.name: s.value
        for s in Setting._base_manager.filter(name__in=_DEMO_SETTINGS)
    }
    if not values.get("DEMO_ACCOUNT_ENABLED"):
        return set()
    return {
        str(values[name]).strip().casefold()
        for name in ("DEMO_ACCOUNT_EMAIL", "DEMO_ACCOUNT_B2B_EMAIL")
        if values.get(name)
    }


def _kept_user_ids(schema: str, public: str) -> frozenset[int]:
    """Accounts whose rows stay untouched.

    In the public schema ``user_useraccount`` IS the platform table:
    staff, superusers and anyone with a tenant membership are operator
    logins (``tenant.membership.is_platform_identity``). In a store's
    schema every account is a customer, bar the shared demo logins the
    storefront prints a sign-in button for.
    """
    from user.models import UserAccount

    users = UserAccount._base_manager
    if schema == public:
        from tenant.models import UserTenantMembership

        members = UserTenantMembership.objects.values("user_id")
        query = Q(is_staff=True) | Q(is_superuser=True) | Q(pk__in=members)
        return frozenset(users.filter(query).values_list("pk", flat=True))
    emails = _demo_emails()
    if not emails:
        return frozenset()
    return frozenset(
        pk
        for pk, email in users.values_list("pk", "email")
        if email and email.casefold() in emails
    )


def _read_schema(name: str, public: str) -> _Schema:
    from user.models import UserAccount

    order_users: dict[int, int | None] = {}
    if name != public:
        from order.models import Order

        order_users = dict(Order._base_manager.values_list("pk", "user_id"))
    return _Schema(
        name=name,
        kept=_kept_user_ids(name, public),
        user_emails={
            pk: email.casefold()
            for pk, email in UserAccount._base_manager.values_list(
                "pk", "email"
            )
            if email
        },
        order_users=order_users,
    )


def _subject(
    rule: rules.Scrub, row: dict[str, Any], label: str, schema: _Schema
) -> tuple[Subject | None, Subject]:
    """``(linked, own)`` subjects of ``row``; ``linked`` None = keep the row.

    ``linked`` follows the row's account or order, so every row of one
    customer resolves one fake person; ``own`` is keyed by the row alone,
    for the fields that belong to someone else (a gift card's sender).
    """
    own_key = f"{label}:{row['pk']}"
    own = Subject(fakes.person(own_key), fakes.place(own_key))
    if rule.is_user:
        user_id: int | None = row["pk"]
        person_key = f"user:{user_id}"
        place_key = own_key
    elif rule.user_fk:
        user_id = row[f"{rule.user_fk}_id"]
        person_key = f"user:{user_id}" if user_id else own_key
        place_key = own_key
    elif rule.order_fk:
        order_id = row[f"{rule.order_fk}_id"]
        user_id = schema.order_users.get(order_id)
        order_key = f"order.Order:{order_id}"
        person_key = f"user:{user_id}" if user_id else order_key
        place_key = order_key if order_id else own_key
    else:
        user_id = None
        person_key = place_key = own_key
    if user_id is not None and user_id in schema.kept:
        return None, own
    return Subject(fakes.person(person_key), fakes.place(place_key)), own


def _fake(
    kind: Kind, original: Any, row_key: str, subject: Subject, name: str
) -> Any:
    person, place = subject.person, subject.place
    match kind:
        case Kind.EMAIL | Kind.ALT_EMAIL:
            return person.email
        case Kind.FIRST_NAME:
            return person.first_name
        case Kind.LAST_NAME:
            return person.last_name
        case Kind.FULL_NAME:
            return person.full_name
        case Kind.USERNAME:
            return person.username
        case Kind.PHONE:
            return person.phone
        case Kind.STREET:
            return place.street
        case Kind.STREET_NUMBER:
            return place.street_number
        case Kind.STREET_LINE:
            return f"{place.street} {place.street_number}"
        case Kind.CITY:
            return place.city
        case Kind.ZIPCODE:
            return place.zipcode
        case Kind.PLACE:
            return place.place
        case Kind.COMPANY:
            return place.company
        case Kind.VAT_ID:
            return place.vat_id
        case Kind.TAX_OFFICE:
            return place.tax_office
        case Kind.ACTIVITY:
            return place.activity
        case Kind.FREE_TEXT:
            return fakes.sentence(f"{row_key}.{name}")
        case Kind.BLANK:
            return ""
        case Kind.NULL:
            return None
        case Kind.IP:
            return fakes.FAKE_IP
        case Kind.TOKEN:
            return fakes.token(f"{row_key}.{name}")
        case Kind.PASSWORD:
            return "!"
        case Kind.FILENAME:
            _stem, ext = os.path.splitext(str(original))
            return f"attachment-{fakes.token(row_key)[-12:]}{ext}"
        case Kind.UUID:
            return fakes.remap_uuid(original)
        case Kind.CODE:
            return fakes.remap_code(original)
    raise AssertionError(f"unhandled kind {kind}")


_SIMPLE_KINDS = frozenset(Kind) - {Kind.TEXT, Kind.JSON}


def _known(
    values: dict[str, Any],
    new_values: dict[str, Any],
    rule: rules.Scrub,
) -> list[tuple[str, str]]:
    """``(original, fake)`` for the row's own identity fields."""
    pairs: list[tuple[str, str]] = []
    for name, spec in rule.fields.items():
        kind = spec.kind if isinstance(spec, F) else spec
        if kind in (Kind.EMAIL, Kind.PHONE, Kind.FULL_NAME) and _present(
            values[name]
        ):
            pairs.append((str(values[name]), str(new_values[name])))
    return pairs


def _collect(
    canaries: scan.Canaries, rule: rules.Scrub, values: dict[str, Any]
) -> None:
    """Remember the originals the scan must not find afterwards."""
    first = last = ""
    for name, spec in rule.fields.items():
        kind = spec.kind if isinstance(spec, F) else spec
        original = values[name]
        if not _present(original):
            continue
        match kind:
            case Kind.EMAIL | Kind.ALT_EMAIL:
                canaries.add_email(str(original))
            case Kind.PHONE:
                canaries.add_phone(str(original))
            case Kind.IP:
                canaries.add_ip(str(original))
            case Kind.UUID | Kind.CODE:
                canaries.add_token(str(original))
            case Kind.FULL_NAME:
                canaries.add_name(*str(original).split())
            case Kind.FIRST_NAME:
                first = str(original)
            case Kind.LAST_NAME:
                last = str(original)
    if first and last:
        canaries.add_name(first, last)


def _emit(emitted: set[str], new_values: dict[str, Any], rule) -> None:
    for name, spec in rule.fields.items():
        kind = spec.kind if isinstance(spec, F) else spec
        if kind in _SIMPLE_KINDS and _present(new_values[name]):
            emitted.add(str(new_values[name]))


def _scrub_rows(
    model: type[Model],
    rule: rules.Scrub,
    schema: _Schema,
    context: _Context,
) -> int:
    label = model._meta.label
    attnames = [
        f"{fk}_id" for fk in (rule.user_fk, rule.order_fk) if fk is not None
    ]
    names = list(rule.fields)
    changed = 0
    batch: list[Model] = []

    def flush() -> None:
        if batch:
            model._base_manager.bulk_update(batch, names, batch_size=_BATCH)
            batch.clear()

    queryset = model._base_manager.order_by("pk")
    if rule.where is not None:
        queryset = queryset.filter(rule.where)
    rows = queryset.values("pk", *attnames, *names)
    for row in rows.iterator(chunk_size=_BATCH):
        linked, own = _subject(rule, row, label, schema)
        if linked is None:
            continue
        row_key = f"{label}:{row['pk']}"
        values = {name: row[name] for name in names}
        new_values = dict(values)
        for name, spec in rule.fields.items():
            kind, owned = (
                (spec.kind, spec.own) if isinstance(spec, F) else (spec, False)
            )
            if kind in (Kind.TEXT, Kind.JSON) or not _present(values[name]):
                continue
            subject = own if owned else linked
            if kind is Kind.ALT_EMAIL and not _is_primary_email(
                row, values[name], schema
            ):
                subject = own
            new_values[name] = _fake(kind, values[name], row_key, subject, name)
        known = _known(values, new_values, rule)
        for name, spec in rule.fields.items():
            kind = spec.kind if isinstance(spec, F) else spec
            if kind not in (Kind.TEXT, Kind.JSON) or not _present(values[name]):
                continue
            if kind is Kind.TEXT:
                new_values[name] = redaction.redact_text(
                    str(values[name]), linked, known
                )
            else:
                new_values[name] = redaction.redact_json(
                    values[name], linked, known
                )
        if new_values == values:
            continue
        _collect(context.canaries, rule, values)
        _emit(context.emitted, new_values, rule)
        batch.append(model(pk=row["pk"], **new_values))
        changed += 1
        if len(batch) >= _BATCH:
            flush()
    flush()
    return changed


def _is_primary_email(row: dict[str, Any], email: str, schema: _Schema) -> bool:
    """Is this allauth address the one its account row carries?"""
    owner = schema.user_emails.get(row["user_id"])
    return owner is not None and str(email).casefold() == owner


def _clear_credentials(
    model: type[Model], rule: rules.Scrub, schema: _Schema, context: _Context
) -> None:
    """Set the rule's credential columns to their cleared value.

    One ``UPDATE``; nothing is read back, so a token that no configured
    key opens is cleared rather than raising ``DecryptionError``.
    """
    if rule.clear is None:
        return
    cleared = model._base_manager.update(**rule.clear())
    if cleared:
        label = model._meta.label
        context.report.cleared[f"{schema.name}: {label}"] += cleared


def _assert_credentials_blank(
    models_here: list[type[Model]], name: str
) -> None:
    """No encrypted column of any model in ``name`` holds a value.

    Derived from the field class, not from the rules: a model that gains
    an ``EncryptedCharField`` and no ``clear`` fails the run here, however
    the rules were written.
    """
    from devtools.anonymise import credentials

    left = []
    for model in models_here:
        for column in credentials.encrypted_fields(model):
            if model._base_manager.exclude(**{column.name: ""}).exists():
                left.append(f"{name}.{model._meta.label}.{column.name}")
    if left:
        raise ResidueFound(
            "encrypted credentials survive in: " + ", ".join(sorted(left))
        )


def _delete_all(
    models: list[type[Model]], schema: _Schema, context: _Context
) -> None:
    """Delete every row of ``models``, children before their parents.

    ``QuerySet.delete`` honours ``on_delete``, so a ``PROTECT`` between two
    models being wiped (dj-stripe has several) raises until the protecting
    side is gone. Retrying the refused models lets the order sort itself
    out; a pass that deletes nothing means a protector outside the set,
    and that is an error, not something to skip.
    """
    pending = list(models)
    while pending:
        refused: list[type[Model]] = []
        for model in pending:
            try:
                with transaction.atomic():
                    deleted = model._base_manager.all().delete()[0]
            except ProtectedError:
                refused.append(model)
                continue
            if deleted:
                label = model._meta.label
                context.report.deleted[f"{schema.name}: {label}"] += deleted
        if len(refused) == len(pending):
            names = sorted(m._meta.label for m in refused)
            raise ProtectedError(f"cannot delete {names}", set())
        pending = refused


def _tenant_schemas() -> list[str]:
    from tenant.models import Tenant

    public = get_public_schema_name()
    with schema_context(public):
        return list(
            Tenant.objects.exclude(schema_name=public)
            .order_by("schema_name")
            .values_list("schema_name", flat=True)
        )


def _assert_no_unknown_schema(known: list[str]) -> None:
    """Every schema in the database must be one this run scans.

    A schema with no ``Tenant`` row (an archived or half-dropped store)
    would be copied across, never scrubbed and never scanned.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT schema_name FROM information_schema.schemata")
        present = {name for (name,) in cursor.fetchall()}
    unknown = sorted(
        name
        for name in present - set(known) - _SYSTEM_SCHEMAS
        if not name.startswith("pg_")
    )
    if unknown:
        raise UnknownSchema(
            f"schemas with no tenant row would keep personal data: {unknown}"
        )


def _kept_values(schema: _Schema, context: _Context) -> None:
    """Values that must never count as leaks: kept accounts' addresses."""
    from user.models import UserAccount

    emails = UserAccount._base_manager.filter(pk__in=schema.kept).values_list(
        "email", flat=True
    )
    context.allowed.update(e for e in emails if e)


def anonymise_database() -> Report:
    """Anonymise the whole database; staging only. Raises, rolling back,
    on any leftover."""
    _require_staging()
    public = get_public_schema_name()
    context = _Context()
    tenants = _tenant_schemas()
    with transaction.atomic():
        _assert_no_unknown_schema([public, *tenants])
        for name in (public, *tenants):
            with schema_context(name):
                schema = _read_schema(name, public)
                context.report.kept_users[name] = len(schema.kept)
                _kept_values(schema, context)
                wipe: list[type[Model]] = []
                for model in rules.concrete_models():
                    rule = rules.resolve(model)
                    if not _in_schema(model, name, public):
                        continue
                    if isinstance(rule, rules.Scrub):
                        changed = _scrub_rows(model, rule, schema, context)
                        if changed:
                            label = model._meta.label
                            context.report.scrubbed[f"{name}: {label}"] += (
                                changed
                            )
                        _clear_credentials(model, rule, schema, context)
                    elif isinstance(rule, rules.Delete) and not (
                        rule.tenant_only and name == public
                    ):
                        wipe.append(model)
                _delete_all(wipe, schema, context)
                _assert_credentials_blank(
                    [
                        model
                        for model in rules.concrete_models()
                        if _in_schema(model, name, public)
                    ],
                    name,
                )
        context.canaries.discard(context.emitted | context.allowed)
        leaks: Counter[str] = Counter()
        if context.canaries:
            for name in (public, *tenants):
                leaks.update(scan.scan_schema(name, context.canaries))
        if leaks:
            raise ResidueFound(
                "original personal values survive in: "
                + ", ".join(
                    f"{where} x{n}" for where, n in sorted(leaks.items())
                )
            )
    return context.report
