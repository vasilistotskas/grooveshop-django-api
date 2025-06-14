import re
from typing import Any

from django.utils.translation import gettext_lazy as _

from core.utils.email import is_disposable_domain


def detect_spam_patterns(message: str, email: str, name: str) -> bool:
    spam_keywords = [
        "viagra",
        "casino",
        "lottery",
        "winner",
        "congratulations",
        "click here",
        "free money",
        "guaranteed",
        "make money fast",
        "no risk",
        "special promotion",
        "limited time",
        "act now",
        "urgent",
        "cheap",
        "discount",
        "lowest price",
        "best deal",
    ]

    message_lower = message.lower()
    spam_score = 0

    for keyword in spam_keywords:
        if keyword in message_lower:
            spam_score += 1

    if spam_score >= 3:
        return True

    if len(message) > 5000:
        return True

    if message.count("http") > 3:
        return True

    if re.search(r"(.)\1{10,}", message):
        return True

    return len(message.split()) < 5


def sanitize_message(message: str) -> str:
    message = re.sub(r"<[^>]+>", "", message)

    message = re.sub(r"(.)\1{5,}", r"\1\1\1", message)

    message = re.sub(r"\s+", " ", message)

    return message.strip()


def validate_contact_content(
    name: str, email: str, message: str
) -> dict[str, Any]:
    errors = {}
    warnings = []

    if len(name.strip()) < 2:
        errors["name"] = _("Name must be at least 2 characters long")

    if len(message.strip()) < 10:
        errors["message"] = _("Message must be at least 10 characters long")

    if len(message) > 5000:
        errors["message"] = _("Message is too long (maximum 5000 characters)")

    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_pattern, email):
        errors["email"] = _("Please provide a valid email address")

    if detect_spam_patterns(message, email, name):
        errors["spam"] = _("Message appears to be spam")

    email_domain = email.split("@")[-1]
    if is_disposable_domain(email_domain):
        errors["email"] = _(
            "Disposable / temporary email addresses are not allowed"
        )

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}
