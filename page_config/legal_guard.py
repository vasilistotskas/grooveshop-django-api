"""A store's legal pages stay servable.

The storefront 404s a legal page (``LEGAL_DOCUMENT_SLUGS``) that is not
published or has no body in the page's language, and checkout refuses
every order, and every card payment, while the terms of use or the
privacy policy cannot be shown (``OrderLegalAcceptanceService``). So
one edit in the admin or the staff API could silently stop a store's
sales. These rules refuse that edit instead, as ``Tenant`` refuses to
serve a language its legal pages lack.

A merchant still rewrites the text freely: only unpublishing,
scheduling for later, emptying a served language, renaming the slug and
deleting are refused. The admin (model ``clean``, delete permissions)
and the staff API (``ContentPageWriteSerializer``, ``destroy``) apply
the same functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise

from page_config.legal_documents import LEGAL_DOCUMENT_SLUGS
from tenant.membership import bound_tenant


@dataclass(frozen=True, slots=True)
class LegalPageError:
    """A refused edit: a stable ``code`` for the API, and why."""

    code: str
    message: StrOrPromise


UNPUBLISHED = _(
    "A legal page must stay published: the storefront hides it, and "
    "checkout refuses every order while the terms of use or privacy "
    "policy cannot be shown. Edit its text instead."
)
SCHEDULED = _(
    "A legal page must be published now, not scheduled for later: the "
    "storefront hides it until then, and checkout refuses every order "
    "meanwhile."
)
RENAMED = _(
    "A legal page keeps its slug: the storefront serves it at a fixed "
    "address, and checkout records it by that slug."
)
EMPTY = _(
    "A legal page needs a body in every language the store serves: the "
    "storefront shows nothing in that language, and checkout refuses "
    "every order there."
)
NOT_DELETABLE = _(
    "A legal page cannot be deleted: the storefront serves it, and "
    "checkout records which version every order accepted. Edit its "
    "text instead."
)


def is_legal_page(slug: str) -> bool:
    return slug in LEGAL_DOCUMENT_SLUGS


def served_locales() -> frozenset[str]:
    """The languages the bound store serves; none outside a store."""
    tenant = bound_tenant()
    return frozenset(tenant.served_locales) if tenant else frozenset()


def publication_error(
    slug: str, *, is_published: bool, published_at: datetime | None
) -> LegalPageError | None:
    """Why a legal page cannot be saved this way, or None."""
    if not is_legal_page(slug):
        return None
    if not is_published:
        return LegalPageError("legal_page_unpublished", UNPUBLISHED)
    if published_at is not None and published_at > timezone.now():
        return LegalPageError("legal_page_scheduled", SCHEDULED)
    return None


def rename_error(saved_slug: str | None, slug: str) -> LegalPageError | None:
    """Why a saved legal page cannot take *slug*, or None."""
    if saved_slug and is_legal_page(saved_slug) and slug != saved_slug:
        return LegalPageError("legal_page_renamed", RENAMED)
    return None


def body_error(
    slug: str, language_code: str, body: str | None
) -> LegalPageError | None:
    """Why a legal page's translation cannot have *body*, or None."""
    if (
        is_legal_page(slug)
        and language_code in served_locales()
        and not (body or "").strip()
    ):
        return LegalPageError("legal_page_empty", EMPTY)
    return None


def translation_deletable(slug: str, language_code: str) -> bool:
    """A legal page's translation in a served language is not."""
    return not (is_legal_page(slug) and language_code in served_locales())
