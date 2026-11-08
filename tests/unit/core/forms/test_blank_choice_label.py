"""Every select's empty option is our label, in the reader's language.

Django 6.1 replaced ``---------`` with ``BLANK_CHOICE_LABEL`` ("- Select
an option -") and ships no Greek for it, so the el admin showed English
in every empty select. ``CoreConfig.ready`` sets Django's constant to
``core.forms.choices.BLANK_CHOICE_LABEL``, a msgid of ours that the el
catalogue translates. These tests fail if that assignment is lost or the
translation goes missing.
"""

from __future__ import annotations

import pytest
from django import forms
from django.db.models import fields
from django.utils import translation

from core.forms.choices import BLANK_CHOICE_LABEL
from order.models import Order


def test_django_uses_our_label():
    assert fields.BLANK_CHOICE_LABEL is BLANK_CHOICE_LABEL


@pytest.mark.parametrize(
    ("language", "expected"),
    [("el", "- Επιλέξτε -"), ("en", "- Select an option -")],
)
def test_a_model_select_shows_the_label_in_the_active_language(
    language, expected
):
    with translation.override(language):
        field = forms.ModelChoiceField(queryset=Order.objects.none())

        assert str(field.empty_label) == expected
