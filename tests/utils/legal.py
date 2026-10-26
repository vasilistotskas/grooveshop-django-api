"""Publish a store's checkout legal documents.

A store that takes orders has its terms of use and privacy policy
published in every language it serves (seeded at provisioning, and
``Tenant`` refuses a locale without them). Checkout records the version
of each a buyer accepts (``OrderLegalAcceptanceService``), and refuses a
payment intent or an order when one cannot be shown. The test database
seeds none, so a checkout test publishes them.
"""

from __future__ import annotations

from django.conf import settings

from order.enum.legal_acceptance import LegalAcceptanceSource
from order.services import LegalAcceptance, OrderLegalAcceptanceService
from page_config.legal_documents import CHECKOUT_CONSENT_SLUGS
from page_config.models import ContentPage, ContentPageTranslation


def publish_checkout_legal_documents() -> dict[str, ContentPage]:
    """Publish each checkout legal document in every configured language.

    Idempotent: a unittest-style class keeps its rows across its tests.
    """
    pages = {}
    for slug in CHECKOUT_CONSENT_SLUGS:
        page, _created = ContentPage.objects.get_or_create(
            slug=slug, defaults={"is_published": True}
        )
        for code, _name in settings.LANGUAGES:
            ContentPageTranslation.objects.get_or_create(
                master=page,
                language_code=code,
                defaults={
                    "title": f"{slug} ({code})",
                    "body": f"<p>{slug} text in {code}</p>",
                },
            )
        pages[slug] = page
    return pages


def checkout_acceptance(language_code: str = "el") -> LegalAcceptance:
    """A buyer's acceptance of the checkout legal texts, as order
    creation takes it (``OrderLegalAcceptanceService.accept``), for a
    test that calls the order service directly. Publishes the texts
    first."""
    publish_checkout_legal_documents()
    return LegalAcceptance(
        source=LegalAcceptanceSource.CHECKOUT_CONSENT,
        documents=OrderLegalAcceptanceService.documents_in_force(language_code),
    )
