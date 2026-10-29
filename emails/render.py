"""Rendering one email: the subject and both bodies, in one language.

The only place that turns an :class:`EmailType` and an object into text, so
the sent mail and the admin preview cannot differ.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from string import Formatter

from django.template.loader import render_to_string
from django.utils import translation
from django.utils.translation import gettext

from core.utils.email_context import build_email_context
from emails.types import EmailType, RenderEnv, subject_fields

logger = logging.getLogger(__name__)

SUBJECT_MAX_LENGTH = 200


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    text: str
    html: str
    language: str


def clean_header_text(
    value: str, *, max_length: int = SUBJECT_MAX_LENGTH
) -> str:
    """*value* made safe for a header: every whitespace run (a CR or LF
    included) becomes one space, the control characters left go, and the
    length is capped. A store-written product or company name in a subject
    can then neither break the header nor start another."""
    collapsed = " ".join(value.split())
    return "".join(
        char for char in collapsed if unicodedata.category(char) != "Cc"
    )[:max_length].rstrip()


def format_subject(template: str, values: Mapping[str, object]) -> str:
    """*template* with its named fields replaced by ``str(values[name])``.

    Nothing is evaluated: a field is looked up by name and stringified, so
    a template can never traverse an object. A name missing from *values*
    raises ``KeyError`` (a builder bug); a template with anything but plain
    named fields raises ``ValueError`` (``types.subject_fields``).
    """
    subject_fields(template)
    return "".join(
        literal if field is None else literal + str(values[field])
        for literal, field, _spec, _conversion in Formatter().parse(template)
    )


def _translated_subject(email_type: EmailType) -> str:
    """The subject's translation, or its msgid when the translation cannot
    be trusted.

    A deliberate, narrow exception to the no-fallback rule: the msgid is
    checked when the type is built, but a msgstr can be edited later (the
    .po, the Rosetta overlay), and one bad translation must not stop an
    email. A msgstr with an invalid field, or one the msgid does not have,
    is logged and replaced by the msgid.
    """
    translated = gettext(email_type.subject)
    try:
        if not subject_fields(translated) <= subject_fields(email_type.subject):
            raise ValueError("it names a field the msgid does not")
    except ValueError as error:
        logger.warning(
            "Subject translation of %s ignored: %s",
            email_type.key,
            error,
            extra={"email_type": email_type.key},
        )
        return email_type.subject
    return translated


def render(email_type: EmailType, obj: object, env: RenderEnv) -> RenderedEmail:
    """The email *obj* produces for the recipient and language in *env*.

    The context builder, the subject and both templates all run under the
    recipient's language, so a lazy label in the context and the subject
    resolve in it whatever language the worker was left in.
    """
    with translation.override(env.language):
        context = build_email_context(
            language=env.language, **email_type.build_context(obj, env)
        )
        subject = clean_header_text(
            format_subject(_translated_subject(email_type), context)
        )
        return RenderedEmail(
            subject=subject,
            text=render_to_string(f"{email_type.templates}.txt", context),
            html=render_to_string(f"{email_type.templates}.html", context),
            language=env.language,
        )
