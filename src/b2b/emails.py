"""The B2B emails: their types, builders and samples.

The customer is told how their application was reviewed; the store's staff
are told one is waiting. ``emails.EmailsConfig`` imports this module at
boot; ``b2b.tasks`` chooses WHEN to send.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop

from b2b.enum import BusinessProfileStatus, ViesStatus
from core.utils.i18n import get_user_language
from emails.declare import customer_email, staff_email
from emails.gates import FeatureGate
from emails.recipients import Recipient
from emails.types import RenderEnv
from tenant.credentials import tenant_site_name

if TYPE_CHECKING:
    from b2b.models import BusinessProfile

TEMPLATE_DIR = "emails/b2b"

#: The key of the email each review outcome sends. A profile in any other
#: status has none (``b2b.tasks.send_business_profile_status_email``).
REVIEW_EMAIL_KEYS = {
    BusinessProfileStatus.APPROVED: "b2b.profile_approved",
    BusinessProfileStatus.REJECTED: "b2b.profile_rejected",
}


def _profile(profile: BusinessProfile, env: RenderEnv) -> dict[str, Any]:
    return {
        "site": tenant_site_name(),
        "company": profile.company_name,
        "profile": profile,
    }


def _applicant(profile: BusinessProfile) -> list[Recipient]:
    address = profile.user.email
    if not address:
        return []
    return [
        Recipient(address=address, language=get_user_language(profile.user))
    ]


def _sample_profile(env: RenderEnv) -> BusinessProfile:
    from b2b.models import BusinessProfile, CustomerGroup
    from user.models import UserAccount

    return BusinessProfile(
        user=UserAccount(
            email=env.recipient.address,
            first_name="Maria",
            last_name="Papadopoulou",
        ),
        status=BusinessProfileStatus.APPROVED,
        company_name="Acme Trading IKE",
        vat_id="094019245",
        tax_office="Α' Αθηνών",
        activity="Retail trade",
        vies_status=ViesStatus.VALID,
        vies_name="ACME TRADING IKE",
        customer_group=CustomerGroup(name="Wholesale"),
    )


_wholesale = FeatureGate(feature="wholesale", label=_("Wholesale feature"))

customer_email(
    "b2b.profile_approved",
    title=_("Business account approved"),
    description=_("Sent when a business application is approved."),
    area="wholesale",
    templates=f"{TEMPLATE_DIR}/business_profile_approved",
    subject=gettext_noop("[{site}] Your business account has been approved"),
    build_context=_profile,
    sample=_sample_profile,
    recipients=_applicant,
    gates=(_wholesale,),
)
customer_email(
    "b2b.profile_rejected",
    title=_("Business account application rejected"),
    description=_("Sent when a business application is rejected."),
    area="wholesale",
    templates=f"{TEMPLATE_DIR}/business_profile_rejected",
    subject=gettext_noop(
        "[{site}] Update on your business account application"
    ),
    build_context=_profile,
    sample=_sample_profile,
    recipients=_applicant,
    gates=(_wholesale,),
)
staff_email(
    "team.b2b_application",
    title=_("New business application"),
    description=_("Tells the store's staff that an application awaits review."),
    templates=f"{TEMPLATE_DIR}/admin_new_business_profile",
    subject=gettext_noop("New B2B application — {company}"),
    build_context=_profile,
    sample=_sample_profile,
    gates=(_wholesale,),
)
