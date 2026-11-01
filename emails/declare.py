"""The three shapes of email type every domain declares.

A domain's ``<app>/emails.py`` registers its types through these, so the
audience, the kind and the resolver that go with each shape are written
once: mail to a customer (transactional, or marketing the customer can
decline) and mail to the store's own staff.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from django_stubs_ext import StrOrPromise

from emails.gates import Gate
from emails.recipients import Recipient, platform_operators, store_operators
from emails.registry import registry
from emails.types import Audience, EmailType, Kind, Owner, RenderEnv

BuildContext = Callable[[Any, RenderEnv], Mapping[str, object]]


def customer_email(
    key: str,
    *,
    title: StrOrPromise,
    description: StrOrPromise,
    area: str,
    templates: str,
    subject: str,
    build_context: BuildContext,
    sample: Callable[[RenderEnv], Any],
    recipients: Callable[[Any], Sequence[Recipient]],
    list_id: str = "",
    required: bool = True,
    kind: Kind = Kind.TRANSACTIONAL,
    gates: tuple[Gate, ...] = (),
) -> EmailType:
    """Mail to a customer. *required* mail cannot be switched off;
    ``Kind.MARKETING`` mail (never required) honours the recipient's
    opt-out and carries the one-click unsubscribe headers."""
    return registry.register(
        EmailType(
            key=key,
            title=title,
            description=description,
            area=area,
            audience=Audience.CUSTOMER,
            kind=kind,
            required=required,
            templates=templates,
            subject=subject,
            build_context=build_context,
            sample=sample,
            recipients=recipients,
            gates=gates,
            list_id=list_id,
        )
    )


def marketing_email(key: str, **fields: Any) -> EmailType:
    """Mail a customer did not ask for in the moment (or asked for once):
    switchable, with an opt-out."""
    return customer_email(key, kind=Kind.MARKETING, required=False, **fields)


def staff_email(
    key: str,
    *,
    title: StrOrPromise,
    description: StrOrPromise,
    templates: str,
    subject: str,
    build_context: BuildContext,
    sample: Callable[[RenderEnv], Any],
    gates: tuple[Gate, ...] = (),
    reply_to: Callable[[Any], Sequence[str]] | None = None,
    area: str = "team",
) -> EmailType:
    """An alert to the bound store's own staff. Always switchable."""
    return registry.register(
        EmailType(
            key=key,
            title=title,
            description=description,
            area=area,
            audience=Audience.STORE_STAFF,
            kind=Kind.TRANSACTIONAL,
            templates=templates,
            subject=subject,
            build_context=build_context,
            sample=sample,
            recipients=lambda obj: store_operators(),
            gates=gates,
            reply_to=reply_to,
        )
    )


def platform_email(
    key: str,
    *,
    title: StrOrPromise,
    description: StrOrPromise,
    audience: Audience,
    templates: str,
    subject: str,
    build_context: BuildContext,
    sample: Callable[[RenderEnv], Any],
    recipients: Callable[[Any], Sequence[Recipient]],
    area: str = "platform",
) -> EmailType:
    """Mail the platform itself sends, from the platform's sender and
    under ``public``: never listed on a store's email page. Always
    *required*: the platform has no switch page, and a billing notice, a
    password reset or an alert to the operators must not be mutable."""
    return registry.register(
        EmailType(
            key=key,
            title=title,
            description=description,
            area=area,
            audience=audience,
            kind=Kind.TRANSACTIONAL,
            owner=Owner.PLATFORM,
            required=True,
            templates=templates,
            subject=subject,
            build_context=build_context,
            sample=sample,
            recipients=recipients,
        )
    )


def operator_alert(
    key: str,
    *,
    title: StrOrPromise,
    description: StrOrPromise,
    templates: str,
    subject: str,
    build_context: BuildContext,
    sample: Callable[[RenderEnv], Any],
) -> EmailType:
    """An alert to the platform's operators (``settings.ADMINS``), in the
    platform's language."""
    return platform_email(
        key,
        title=title,
        description=description,
        audience=Audience.PLATFORM_OPERATOR,
        templates=templates,
        subject=subject,
        build_context=build_context,
        sample=sample,
        recipients=lambda obj: platform_operators(),
    )
