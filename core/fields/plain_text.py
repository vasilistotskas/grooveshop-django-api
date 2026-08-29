from __future__ import annotations

import re
from html import unescape
from typing import Any

from django.db import models
from django.utils.html import strip_tags

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_plain_text(value: Any) -> Any:
    """Reduce ``value`` to single-spaced, tag-free, entity-decoded text.

    Non-strings (``None`` included) pass through untouched so the field
    keeps Django's usual null/default handling.
    """
    if not isinstance(value, str):
        return value

    text = unescape(strip_tags(value))
    return _WHITESPACE_RE.sub(" ", text).strip()


class PlainTextField(models.TextField):
    """A ``TextField`` whose value is always stored as plain text.

    For content that ends up inside an HTML *attribute* — a meta
    description, an og:description — markup is never renderable, only
    corrupting: the storefront emitted
    ``<meta name="description" content="<div>…</div>">`` for 40% of blog
    posts because editors pasted rich text into the field. Normalizing at
    the field level covers every write path (admin, DRF, imports, shell)
    for every model that inherits ``SeoModel``, instead of asking each
    caller to remember.

    ``pre_save`` is the hook rather than ``clean()`` so the guarantee
    holds for saves that never run model validation, which is most of
    them.
    """

    def pre_save(self, model_instance: models.Model, add: bool) -> Any:
        value = normalize_plain_text(getattr(model_instance, self.attname))
        setattr(model_instance, self.attname, value)
        return value
