"""The catalogue of email types.

Domain apps register their types at import (``<app>/emails.py``, found by
``EmailsConfig.ready``), and ``freeze`` then checks the whole set once.
Nothing registers later, and nothing reads the catalogue before it is
frozen.
"""

from __future__ import annotations

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.template import engines
from django.template.exceptions import TemplateDoesNotExist
from django.template.loader import get_template
from django.template.utils import get_app_template_dirs

from emails.types import EmailType, Owner

#: What a type's ``templates`` names: one file per body.
TEMPLATE_SUFFIXES = (".html", ".txt")


class UnknownEmailType(KeyError):
    """No type is registered under the key."""


def _template_files(directory: str) -> set[str]:
    """``directory/<name>`` for every ``.html`` or ``.txt`` file that sits
    directly in the template directory *directory*, in any template root."""
    engine = engines["django"].engine
    roots = [*engine.dirs, *get_app_template_dirs("templates")]
    return {
        f"{directory}/{path.stem}"
        for root in roots
        for path in (Path(root) / directory).glob("*")
        if path.suffix in TEMPLATE_SUFFIXES
    }


class Registry:
    def __init__(self) -> None:
        self._types: dict[str, EmailType] = {}
        self._frozen = False

    def register(self, email_type: EmailType) -> EmailType:
        if self._frozen:
            raise ImproperlyConfigured(
                f"Email type {email_type.key!r} registered after the "
                "catalogue was frozen: declare it in <app>/emails.py."
            )
        if email_type.key in self._types:
            raise ImproperlyConfigured(
                f"Email type {email_type.key!r} is registered twice."
            )
        self._types[email_type.key] = email_type
        return email_type

    def freeze(self) -> None:
        """Check the set as a whole, then lock it.

        Every type's template pair must exist. A template file sitting in
        a directory some type already uses, with no type naming it, is a
        leftover and refused: only directories the catalogue owns are
        scanned, so the templates of mail that has not moved into it yet
        are none of its business.
        """
        problems = []
        referenced = {t.templates for t in self._types.values()}
        for base in sorted(referenced):
            for suffix in TEMPLATE_SUFFIXES:
                try:
                    get_template(f"{base}{suffix}")
                except TemplateDoesNotExist:
                    problems.append(f"template {base}{suffix} does not exist")
        for directory in sorted(
            {base.rpartition("/")[0] for base in referenced}
        ):
            for base in sorted(_template_files(directory) - referenced):
                problems.append(f"template {base} is used by no email type")
        if problems:
            raise ImproperlyConfigured(
                "Invalid email catalogue: " + "; ".join(problems) + "."
            )
        self._frozen = True

    def get(self, key: str) -> EmailType:
        try:
            return self._types[key]
        except KeyError:
            raise UnknownEmailType(key) from None

    def all_types(self) -> tuple[EmailType, ...]:
        return tuple(self._types.values())

    def types_for(self, owner: Owner) -> tuple[EmailType, ...]:
        return tuple(t for t in self._types.values() if t.owner is owner)


registry = Registry()
