from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from tinymce.models import HTMLField

from core.utils.sanitize import removed_markup, sanitize_html

# What TinyMCE's media dialog inserts for a video it cannot turn into an
# allowed player: a file URL, or a host outside EMBED_IFRAME_ORIGINS.
_EMBED_ELEMENTS = ("<iframe", "<video", "<audio", "<source", "<object")


def validate_rich_text(value: Any) -> None:
    """Refuse markup the rich-text policy would strip on save.

    Without this the loss was silent: the editor kept showing an
    embedded video, the save stripped it, and the storefront rendered
    an empty paragraph where it had been.
    """
    if not isinstance(value, str):
        return
    removed = removed_markup(value)
    if not removed:
        return
    errors = [
        ValidationError(
            _(
                "This content contains markup that cannot be published "
                "and would be removed: %(markup)s."
            ),
            code="unsupported_markup",
            params={"markup": ", ".join(removed)},
        )
    ]
    if any(key.startswith(_EMBED_ELEMENTS) for key in removed):
        errors.append(
            ValidationError(
                _("Only YouTube and Vimeo videos can be embedded."),
                code="unsupported_embed",
            )
        )
    raise ValidationError(errors)


class RichTextField(HTMLField):
    """An ``HTMLField`` held to the policy in ``core.utils.sanitize``.

    Two halves, because they guard different things:

    - ``validate_rich_text`` runs wherever validation does (the admin,
      DRF serializers) and turns anything the policy would drop into a
      form error the editor can act on.
    - ``pre_save`` sanitises on every write path, including the ones
      that never validate (imports, the shell, data migrations), since
      the storefront renders this as HTML.

    Normally the two agree and ``pre_save`` changes nothing; it only
    removes markup that reached the model without being validated.
    """

    default_validators = [validate_rich_text]

    def pre_save(self, model_instance: models.Model, add: bool) -> Any:
        value = getattr(model_instance, self.attname)
        if isinstance(value, str):
            value = sanitize_html(value)
            setattr(model_instance, self.attname, value)
        return value
