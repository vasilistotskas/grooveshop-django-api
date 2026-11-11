"""The platform's mail to a store: billing notices and the staff password
reset. Their types, builders and samples.

Sent by the platform, from ``public``, in the store's own language:
``Owner.PLATFORM`` types are never listed on a store's email page.
``emails.EmailsConfig`` imports this module at boot; ``tenant.billing`` and
``tenant.tasks`` choose WHEN to send and keep the bookkeeping after a send.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop
from django_stubs_ext import StrOrPromise

from emails.declare import operator_alert, platform_email
from emails.recipients import Recipient
from emails.types import Audience, RenderEnv

if TYPE_CHECKING:
    from tenant.models import Tenant

BILLING_TEMPLATE_DIR = "emails/billing"

#: The sample store of the previews: an unsaved row, so nothing is read.
SAMPLE_STORE_NAME = "Acme Gadgets"
SAMPLE_OWNER_EMAIL = "owner@acme.example"


@dataclass(frozen=True, slots=True)
class BillingNotice:
    """A dunning notice to a store's owner about its plan term."""

    tenant: Tenant
    grace_days: int


@dataclass(frozen=True, slots=True)
class BillingStageCopy:
    """The operators' record that *tenant* reached billing *stage*."""

    tenant: Tenant
    stage: int
    #: The stage's name (``expiring``, ``expired``, ``suspended``).
    slug: str


@dataclass(frozen=True, slots=True)
class StaffPasswordReset:
    """A staff member's "forgot password" link."""

    address: str
    #: The language of the store's admin that was asked, link included.
    language: str
    reset_url: str
    valid_minutes: int
    #: The store the staff member asked from, read in that store's schema:
    #: the mail is built under ``public``, which knows only the platform.
    store_name: str


def _store_name(tenant: Tenant) -> str:
    return tenant.store_name or tenant.name


def _owner(notice: BillingNotice) -> list[Recipient]:
    tenant = notice.tenant
    return [
        Recipient(
            address=tenant.owner_email,
            language=tenant.default_locale or settings.LANGUAGE_CODE,
        )
    ]


def _notice(notice: BillingNotice, env: RenderEnv) -> dict[str, Any]:
    from tenant.models import TenantPlan

    tenant = notice.tenant
    store = _store_name(tenant)
    return {
        "store": store,
        "store_name": store,
        "plan_display": tenant.get_plan_display(),
        "is_trial": tenant.plan == TenantPlan.TRIAL,
        "paid_until": tenant.paid_until,
        "date": date_format(tenant.paid_until),
        "grace_days": notice.grace_days,
    }


def _stage_copy(copy: BillingStageCopy, env: RenderEnv) -> dict[str, Any]:
    from tenant.credentials import tenant_site_name

    tenant = copy.tenant
    return {
        "site": tenant_site_name(),
        "store_name": _store_name(tenant),
        "schema": tenant.schema_name,
        "plan": tenant.plan,
        "paid_until": tenant.paid_until,
        "stage": copy.stage,
        "slug": copy.slug,
    }


def _staff_reset(reset: StaffPasswordReset, env: RenderEnv) -> dict[str, Any]:
    return {
        "store_name": reset.store_name,
        "reset_url": reset.reset_url,
        "valid_minutes": reset.valid_minutes,
    }


def _sample_notice(env: RenderEnv) -> BillingNotice:
    from tenant.models import Tenant, TenantPlan

    return BillingNotice(
        tenant=Tenant(
            schema_name="acme",
            name="acme",
            store_name=SAMPLE_STORE_NAME,
            owner_email=SAMPLE_OWNER_EMAIL,
            plan=TenantPlan.PRO,
            paid_until=timezone.localdate() + timedelta(days=7),
            default_locale=env.language,
        ),
        grace_days=7,
    )


def _sample_stage_copy(env: RenderEnv) -> BillingStageCopy:
    return BillingStageCopy(
        tenant=_sample_notice(env).tenant, stage=2, slug="expired"
    )


def _sample_staff_reset(env: RenderEnv) -> StaffPasswordReset:
    return StaffPasswordReset(
        address="staff@acme.example",
        language=env.language,
        reset_url="https://admin.acme.example/admin/reset/Mg/token/",
        valid_minutes=settings.PASSWORD_RESET_TIMEOUT // 60,
        store_name=SAMPLE_STORE_NAME,
    )


def _billing_notice(
    key: str,
    *,
    slug: str,
    title: StrOrPromise,
    description: StrOrPromise,
    subject: str,
) -> None:
    platform_email(
        key,
        title=title,
        description=description,
        audience=Audience.STORE_STAFF,
        templates=f"{BILLING_TEMPLATE_DIR}/{slug}",
        subject=subject,
        build_context=_notice,
        sample=_sample_notice,
        recipients=_owner,
    )


_billing_notice(
    "platform.billing_expiring",
    slug="expiring",
    title=_("Plan expiring"),
    description=_(
        "Tells a store's owner that the plan term ends soon, before the "
        "grace period starts."
    ),
    subject=gettext_noop("{store} — your plan expires on {date}"),
)
_billing_notice(
    "platform.billing_expired",
    slug="expired",
    title=_("Plan expired"),
    description=_(
        "Tells a store's owner that the plan term has ended and when the "
        "store will be suspended."
    ),
    subject=gettext_noop("{store} — your plan has expired"),
)
_billing_notice(
    "platform.billing_suspended",
    slug="suspended",
    title=_("Store suspended"),
    description=_(
        "Tells a store's owner that the store was suspended for an unpaid "
        "plan and how to bring it back."
    ),
    subject=gettext_noop("{store} — your store has been suspended"),
)
operator_alert(
    "platform.billing_stage_copy",
    title=_("Billing stage reached"),
    description=_(
        "Tells the platform's operators that a store reached the expired "
        "or the suspended billing stage."
    ),
    templates="emails/billing/stage_copy",
    subject=gettext_noop("[{site}] Tenant billing stage {stage}: {schema}"),
    build_context=_stage_copy,
    sample=_sample_stage_copy,
)
platform_email(
    "platform.staff_password_reset",
    title=_("Staff password reset"),
    description=_(
        "The link a store's staff member asked for to choose a new admin "
        "password."
    ),
    audience=Audience.STORE_STAFF,
    templates="emails/staff/password_reset",
    subject=gettext_noop("Choose a new password for {store_name}"),
    build_context=_staff_reset,
    sample=_sample_staff_reset,
    recipients=lambda reset: [Recipient(reset.address, reset.language)],
)
