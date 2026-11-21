"""The tag app's cache surfaces, which
``core.cache.surfaces.register_default_surfaces`` registers at boot."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface

SURFACES = (
    CacheSurface(
        code="tags",
        label=_("Tags"),
        description=_(
            "Generic Tag list/detail and the M2M-through"
            " TaggedItem cache. Tags are shared by Products and"
            " Blog posts so this surface lives outside both."
        ),
        django_patterns=("*TagViewSet_*", "*TaggedItemViewSet_*"),
        nuxt_patterns=(),
        invalidated_by=(
            "tag.Tag",
            "tag.TagTranslation",
            "tag.TaggedItem",
        ),
        icon="sell",
        group="content",
    ),
)
