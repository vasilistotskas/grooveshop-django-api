"""A freshly provisioned tenant ends up on the current legal text.

A new schema is migrated from scratch: 0021 seeds the legal documents
with the text of its day, 0022 corrects their identity, and 0032 must
then recognise that output and bring the privacy policy forward. The
main suite runs every migration on ``public`` only, where all three
return early, so this chain can only be observed here. If any link
stops matching the one before it, new stores are provisioned with a
privacy policy that omits the server logs, and flagged as behind from
their first day.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django_tenants.utils import schema_context


@pytest.mark.django_db
def test_fresh_tenant_privacy_policy_is_current(mt_tenant):
    from page_config.defaults import pending_legal_reviews
    from page_config.legal_documents import LEGAL_TEXT_REVISION
    from page_config.models import ContentPage

    with schema_context(mt_tenant.schema_name):
        page = ContentPage.objects.get(slug="privacy")
        body = page.translations.get(
            language_code=settings.PARLER_DEFAULT_LANGUAGE_CODE
        ).body

        assert '<section id="server-logs">' in body
        assert page.legal_text_revision == LEGAL_TEXT_REVISION
        assert pending_legal_reviews() == []
