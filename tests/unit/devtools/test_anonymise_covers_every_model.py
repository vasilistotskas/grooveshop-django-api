"""Every model has a decided outcome in the staging anonymiser.

The refresh copies production into staging, so a model nobody thought
about is a model whose customers' data reaches staging unchanged. Same
shape as ``tests/unit/user/test_erasure_covers_every_user_relation.py``,
and for the same reason: forgetting is silent.

Three questions, each with its own failure message:

* is every model scrubbed, deleted or named as holding nothing personal?
* does every field that LOOKS personal (an e-mail, a phone, an IP, a
  name-shaped column) have a rule, or a stated reason to be kept?
* does everything GDPR erasure handles also get handled here?

``scan.py`` is the runtime backstop for what none of these can see; these
tests are what makes a new model a CI failure instead of a refresh-time
surprise.
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField

from devtools.anonymise import rules
from tests.unit.user.test_erasure_covers_every_user_relation import (
    _handled_model_names,
)

User = get_user_model()

_PERSONAL_TYPES = (
    models.EmailField,
    PhoneNumberField,
    models.GenericIPAddressField,
)
# Name-shaped columns the field TYPE does not reveal.
_PERSONAL_NAMES = frozenset(
    {
        "first_name",
        "last_name",
        "full_name",
        "email",
        "phone",
        "ip_address",
        "user_agent",
        "street",
        "street_number",
        "address",
        "city",
        "zipcode",
        "postal_code",
        "birth_date",
        "session_key",
        "session_id",
        "recipient_name",
        "sender_name",
        "company_name",
        "vat_id",
        "bio",
    }
)


def _is_personal_looking(field: models.Field) -> bool:
    return isinstance(field, _PERSONAL_TYPES) or field.name in _PERSONAL_NAMES


def test_every_model_is_scrubbed_deleted_or_declared_not_personal():
    undecided = sorted(
        model._meta.label
        for model in apps.get_models()
        if rules.resolve(model) is None
    )

    assert not undecided, (
        "These models have no staging-anonymisation decision. Add a "
        "`Scrub` or `Delete` to `devtools.anonymise.rules.REGISTRY`, or "
        "list the model in `NOT_PERSONAL` with the reason it holds nothing "
        "about a person:\n  " + "\n  ".join(undecided)
    )


def test_no_decision_names_a_model_that_is_gone_or_is_made_twice():
    labels = {model._meta.label for model in apps.get_models()}
    stale = sorted((set(rules.REGISTRY) | set(rules.NOT_PERSONAL)) - labels)
    twice = sorted(set(rules.REGISTRY) & set(rules.NOT_PERSONAL))
    wipe_apps = {model._meta.app_label for model in apps.get_models()}
    wipe_names = {
        f"{app}.{name}"
        for app, names in rules.WIPE_APPS.items()
        for name in names
    }

    assert not stale, f"These models no longer exist: {stale}"
    assert not twice, f"Decided twice (rule AND not-personal): {twice}"
    assert set(rules.WIPE_APPS) <= wipe_apps
    assert wipe_names <= labels, (
        f"Unknown wipe exceptions: {wipe_names - labels}"
    )


@pytest.mark.parametrize(
    ("label", "rule"),
    sorted(
        (label, rule)
        for label, rule in rules.REGISTRY.items()
        if isinstance(rule, rules.Scrub)
    ),
)
def test_a_scrub_names_real_fields_and_links(label, rule):
    model = apps.get_model(label)
    fields = {f.name: f for f in model._meta.concrete_fields}

    assert set(rule.fields) <= set(fields), (
        f"{label}: no such field(s) {set(rule.fields) - set(fields)}"
    )
    if rule.user_fk:
        assert fields[rule.user_fk].related_model is User
    if rule.order_fk:
        assert fields[rule.order_fk].related_model._meta.label == "order.Order"


def test_every_personal_looking_field_is_scrubbed_or_explained():
    undecided = []
    for model in apps.get_models():
        rule = rules.resolve(model)
        if isinstance(rule, rules.Delete):
            continue
        scrubbed = set(rule.fields) if isinstance(rule, rules.Scrub) else set()
        for field in model._meta.concrete_fields:
            label = f"{model._meta.label}.{field.name}"
            if (
                _is_personal_looking(field)
                and field.name not in scrubbed
                and label not in rules.NOT_PERSONAL_FIELDS
            ):
                undecided.append(label)

    assert not undecided, (
        "These fields look personal but are neither scrubbed nor named in "
        "`rules.NOT_PERSONAL_FIELDS` with a reason:\n  "
        + "\n  ".join(sorted(undecided))
    )


def test_no_field_exemption_outlives_its_field():
    live = {
        f"{model._meta.label}.{field.name}"
        for model in apps.get_models()
        for field in model._meta.concrete_fields
    }
    stale = sorted(set(rules.NOT_PERSONAL_FIELDS) - live)

    assert not stale, f"These fields are gone; drop their exemptions: {stale}"


@pytest.mark.parametrize(
    ("label", "reason"),
    sorted({**rules.NOT_PERSONAL, **rules.NOT_PERSONAL_FIELDS}.items()),
)
def test_every_exemption_states_a_reason(label, reason):
    assert len(reason) > 20, f"{label} needs a real reason, not '{reason}'"


def test_everything_gdpr_erasure_handles_is_not_exempt_here():
    """A model erasure treats as personal cannot be "not personal" here."""
    handled = _handled_model_names()
    assert handled, "Parsed no querysets: the detector is broken."

    exempt = sorted(
        model._meta.label
        for model in apps.get_models()
        if model.__name__ in handled and isinstance(rules.resolve(model), str)
    )

    assert not exempt, (
        "GDPR erasure handles these models but the staging anonymiser "
        f"declares them not personal: {exempt}"
    )
