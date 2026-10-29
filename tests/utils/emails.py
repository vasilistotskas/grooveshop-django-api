"""Test-only email types, and a catalogue to register them in.

Production registers nothing until the send sites move onto the pipeline,
so the registry, the gates and the pipeline are tested over probe types
that live here, never in production code. The ``catalogue`` fixture swaps
the registry's contents for the test and puts them back after, and makes
the probe templates (``tests/templates/emails_test/``) loadable.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from emails.recipients import Recipient
from emails.registry import registry
from emails.types import Audience, EmailType, Kind

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "templates"
PROBE_TEMPLATES = "emails_test/probe"

#: An existing msgid with a Greek translation, so a render in ``el`` proves
#: the subject is translated under the recipient's language.
TRANSLATED_SUBJECT = "Your Order #{order_id} Has Shipped"


def probe_object(**overrides) -> SimpleNamespace:
    """What the probe types are about: a marker the body prints, an id the
    subject prints, and the customer."""
    return SimpleNamespace(
        **{
            "marker": "MARKER",
            "order_id": 7,
            "email": "customer@example.test",
            "language": "en",
            **overrides,
        }
    )


def probe(key: str = "probe.notice", **overrides) -> EmailType:
    fields = {
        "key": key,
        "title": "Probe",
        "description": "A probe type.",
        "area": "probes",
        "audience": Audience.CUSTOMER,
        "kind": Kind.TRANSACTIONAL,
        "templates": PROBE_TEMPLATES,
        "subject": TRANSLATED_SUBJECT,
        "build_context": lambda obj, env: {
            "marker": obj.marker,
            "order_id": obj.order_id,
        },
        "sample": lambda env: probe_object(),
        "recipients": lambda obj: [Recipient(obj.email, obj.language)],
        **overrides,
    }
    return EmailType(**fields)


def add_template_root(settings, root: Path) -> None:
    """Make *root* a template directory. Assigned whole: the template
    engines are rebuilt when the setting is replaced, not when it is
    edited in place."""
    templates = copy.deepcopy(settings.TEMPLATES)
    templates[0]["DIRS"] = [*templates[0]["DIRS"], str(root)]
    settings.TEMPLATES = templates


@pytest.fixture
def probe_templates(settings) -> None:
    """The probe templates, loadable beside the project's own."""
    add_template_root(settings, TEMPLATE_ROOT)


@pytest.fixture
def catalogue(
    monkeypatch, probe_templates
) -> Callable[..., tuple[EmailType, ...]]:
    """``catalogue(*types)`` registers *types* in an empty registry and
    freezes it; the registry's own contents return after the test."""
    monkeypatch.setattr(registry, "_types", {})
    monkeypatch.setattr(registry, "_frozen", False)

    def register(*types: EmailType) -> tuple[EmailType, ...]:
        for email_type in types:
            registry.register(email_type)
        registry.freeze()
        return types

    return register
