"""
Custom Rosetta storage backend that clears translation cache on save.
"""

from django.core.cache import cache
from django.utils.translation import trans_real
from rosetta.storage import CacheRosettaStorage


class CacheClearingRosettaStorage(CacheRosettaStorage):
    """
    Custom Rosetta storage that clears Django's translation cache
    whenever translations are saved.

    This ensures that updated translations are immediately visible
    across all processes in a multi-process environment.
    """

    def set(self, key, val):
        """Override set to clear translation cache after saving."""
        result = super().set(key, val)

        trans_real._translations = {}
        trans_real._default = None
        trans_real._active = None

        return result
