"""The emails about a contact or feedback submission: their types,
builders and samples.

Both go to the store's own staff, in the store's language, and are
answered to the submitter (``reply_to``). Files are never attached: the
mail only says they exist. ``emails.EmailsConfig`` imports this module at
boot; ``contact.tasks`` chooses WHEN to send.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop

from emails.declare import staff_email
from emails.gates import FeatureGate
from emails.types import RenderEnv

if TYPE_CHECKING:
    from contact.models import Contact, Feedback

logger = logging.getLogger(__name__)

TEMPLATE_DIR = "emails/contact"


def _submitter(submission: Contact | Feedback) -> list[str]:
    """``Reply-To`` for the store's copy of a submission: the submitter.

    A form submission is how a stranger reaches the store, so pressing
    reply must answer them. The address is validated before it reaches a
    header: anything that is not one bare, well-formed address (a line
    break, a display name, a second recipient) is dropped and the mail
    goes out without ``Reply-To``, rather than carrying text a stranger
    typed into a header.
    """
    address = submission.email
    if not address:
        return []
    try:
        validate_email(address)
    except ValidationError:
        logger.warning(
            "Submitter address is not a valid email address — sending the "
            "notification without Reply-To",
            extra={"submission": submission.pk},
        )
        return []
    return [address]


def _contact(contact: Contact, env: RenderEnv) -> dict[str, Any]:
    # ``contact`` here is the row, not the package: the FK's related_name.
    # The bytes are anonymous uploads and may be unscanned, so they stay in
    # the private tree and staff fetch them from the admin.
    return {
        "name": contact.name,
        "contact": contact,
        "attachments": (
            [] if contact._state.adding else list(contact.attachments.all())
        ),
    }


def _feedback(feedback: Feedback, env: RenderEnv) -> dict[str, Any]:
    return {
        "rating": feedback.rating,
        "category": feedback.get_category_display(),
        "feedback": feedback,
    }


def _sample_contact(env: RenderEnv) -> Contact:
    from contact.models import Contact

    return Contact(
        name="Nikos Georgiou",
        email="customer@example.com",
        company="Georgiou SA",
        phone="+302101234567",
        subject="Shipping to Crete",
        message="Do you ship to Crete?",
    )


def _sample_feedback(env: RenderEnv) -> Feedback:
    from contact.models import Feedback, FeedbackCategory

    return Feedback(
        name="Nikos Georgiou",
        email="customer@example.com",
        rating=4,
        category=FeedbackCategory.GENERAL,
        message="Quick checkout, thank you.",
    )


staff_email(
    "team.contact_message",
    title=_("Contact form message"),
    description=_(
        "Tells the store's staff that someone used the contact form."
    ),
    templates=f"{TEMPLATE_DIR}/contact_notification",
    subject=gettext_noop("New contact form submission from {name}"),
    build_context=_contact,
    sample=_sample_contact,
    reply_to=_submitter,
)
staff_email(
    "team.feedback",
    title=_("Feedback received"),
    description=_("Tells the store's staff that someone left feedback."),
    templates=f"{TEMPLATE_DIR}/feedback_notification",
    subject=gettext_noop("New feedback ({rating}★ {category}) submission"),
    build_context=_feedback,
    sample=_sample_feedback,
    gates=(FeatureGate(feature="feedback", label=_("Feedback feature")),),
    reply_to=_submitter,
)
