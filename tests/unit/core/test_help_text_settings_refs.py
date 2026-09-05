"""A help_text that names a setting must name one that exists.

Nineteen `Tenant` credential fields told the operator "Empty falls back
to settings.X." for an X that had never existed — or, in the Viva Wallet
and ACS cases, for one that was deliberately REMOVED when per-tenant
credentials landed, precisely so money could never route through
platform-wide env vars the merchant had not configured for their own
store. `tenant/credentials.py` says so in as many words: "Tenant-only —
NO platform-wide fallback."

That made the admin form actively misleading on the fields where being
misled is most expensive. An operator reading the help_text would leave
`acs_company_password` blank expecting a platform default, and get an
integration that silently disables itself for their store.

Nothing enforced the claim, because a help_text is prose. This does: any
`settings.NAME` a first-party model mentions has to resolve.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings
from django.utils import translation

_REPO_ROOT = Path(__file__).resolve().parents[3]

# `settings.FOO`, `settings.FOO_BAR` — screaming snake case only, so
# prose like "the settings module" or "settings.py" never matches.
_SETTING_REF = re.compile(r"\bsettings\.([A-Z][A-Z0-9_]{2,})\b")


def _is_first_party(model) -> bool:
    try:
        path = Path(model._meta.app_config.path).resolve()
    except AttributeError, TypeError:
        return False
    if "site-packages" in path.parts or ".venv" in path.parts:
        return False
    return _REPO_ROOT in path.parents or path == _REPO_ROOT


def _referenced_settings() -> list[tuple[str, str]]:
    """(field label, setting name) for every reference in a help_text."""
    found = []
    # English is the msgid language; a translated catalogue would still
    # carry the identifier, but reading the source language keeps this
    # independent of which locale happens to be active.
    with translation.override("en"):
        for model in apps.get_models():
            if model._meta.abstract or not _is_first_party(model):
                continue
            for field in model._meta.get_fields():
                text = str(getattr(field, "help_text", "") or "")
                for name in _SETTING_REF.findall(text):
                    label = f"{model._meta.label}.{field.name}"
                    found.append((label, name))
    return sorted(set(found))


_REFERENCES = _referenced_settings()


@pytest.mark.parametrize(
    ("label", "name"),
    _REFERENCES,
    ids=[f"{label}->{name}" for label, name in _REFERENCES],
)
def test_help_text_names_a_setting_that_exists(label, name):
    assert hasattr(settings, name), (
        f"{label} tells the operator about `settings.{name}`, which does "
        f"not exist. On a credential field that is worse than silence: "
        f"it invites leaving the field blank in expectation of a "
        f"platform-wide default that will never be consulted."
    )


def test_the_check_has_something_to_check():
    """Guard against the parametrize list going quietly empty."""
    assert _REFERENCES, (
        "No help_text references a setting at all — either they were all "
        "removed, or the scan stopped matching."
    )
