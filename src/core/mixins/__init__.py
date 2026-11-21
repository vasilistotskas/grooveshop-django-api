"""Core mixins composed into an app's own querysets and managers.

- ``SoftDeleteQuerySetMixin`` and ``SoftDeleteManagerMixin``
  (``core.mixins.soft_delete``): soft delete, the one implementation.
"""

from core.mixins.soft_delete import (
    SoftDeleteManagerMixin,
    SoftDeleteQuerySetMixin,
)

__all__ = [
    "SoftDeleteManagerMixin",
    "SoftDeleteQuerySetMixin",
]
