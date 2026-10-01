"""Every dialog form renders Unfold's widgets.

``unfold.forms.BaseDialogForm`` applies none of its own, and a plain
Django widget renders with no Unfold classes: no border, a transparent
background, an input the eye cannot find (the loyalty and gift-card
adjustment dialogs on staging, 2026-10-01). Unfold's docs: "It is
important to set up a widget from Unfold."
"""

from __future__ import annotations

from django import forms
from unfold.forms import BaseDialogForm


def _subclasses(cls):
    for subclass in cls.__subclasses__():
        yield subclass
        yield from _subclasses(subclass)


def _first_party_dialog_forms():
    return [
        form
        for form in _subclasses(BaseDialogForm)
        if not form.__module__.startswith("unfold")
    ]


def test_dialog_forms_are_found():
    assert _first_party_dialog_forms()


def test_every_visible_dialog_field_uses_an_unfold_widget():
    plain = [
        f"{form.__module__}.{form.__name__}.{name}: "
        f"{type(field.widget).__name__}"
        for form in _first_party_dialog_forms()
        for name, field in form.base_fields.items()
        if not isinstance(field.widget, forms.HiddenInput)
        and not type(field.widget).__module__.startswith("unfold")
    ]
    assert plain == []
