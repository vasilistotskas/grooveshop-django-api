"""The admin is Greek-first: every field a first-party model declares
carries a translatable ``verbose_name``. Without one Django derives the
label from the field name (``pay_way`` -> "pay way"), a plain ``str``
that no catalogue can translate, so the Greek admin shows English.

Fields declared by a third-party class are not ours to label: an
abstract base outside this repository (``knox.AbstractAuthToken``,
``django_tenants.DomainMixin``) and the tree columns django-mptt adds.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.db import models

ROOT = Path(settings.BASE_DIR).resolve()
TEST_PACKAGES = ("tests.", "tests_mt.")


def _is_first_party(path: str | Path) -> bool:
    resolved = Path(path).resolve()
    return resolved.is_relative_to(ROOT) and ".venv" not in resolved.parts


def _declaring_class(model, field) -> type[models.Model]:
    """The abstract base that declared ``field``, else ``model``.

    Django copies an abstract base's fields onto each child, so the
    first (most basic) abstract class in the MRO that lists the field
    is where it was written.
    """
    for base in reversed(model.__mro__):
        meta = getattr(base, "_meta", None)
        if meta is None or not meta.abstract:
            continue
        declared = meta.local_fields + meta.local_many_to_many
        if any(f.name == field.name for f in declared):
            return base
    return model


def _is_excluded(model, field) -> bool:
    if field.name in {"id", "master"}:  # pk; parler's translations FK
        return True
    if field.name.endswith("_currency"):  # djmoney companion column
        return True
    if model.__name__.startswith("Historical"):  # simple-history copies
        return True
    if field.name.startswith("history_"):
        return True
    mptt_meta = getattr(model, "_mptt_meta", None)
    if mptt_meta is not None and field.name in {
        mptt_meta.left_attr,
        mptt_meta.right_attr,
        mptt_meta.tree_id_attr,
        mptt_meta.level_attr,
    }:
        return True
    return not _is_first_party(inspect.getfile(_declaring_class(model, field)))


def _unlabelled_fields() -> list[str]:
    hits = []
    for config in apps.get_app_configs():
        if not _is_first_party(config.path):
            continue
        for model in config.get_models():
            # A model a test declares registers under a real app label
            # for the rest of that worker's session; it is no field of
            # ours to label.
            if model.__module__.startswith(TEST_PACKAGES):
                continue
            for field in model._meta.get_fields():
                if field.auto_created or not (
                    getattr(field, "concrete", False) or field.many_to_many
                ):
                    continue
                label = getattr(field, "verbose_name", None)
                if (
                    type(label) is str
                    and label == field.name.replace("_", " ")
                    and not _is_excluded(model, field)
                ):
                    hits.append(f"{config.label}.{model.__name__}.{field.name}")
    return hits


def test_every_first_party_field_has_a_translatable_label():
    assert _unlabelled_fields() == []
