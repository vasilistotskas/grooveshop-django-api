"""
Rosetta hooks to handle translation cache invalidation.
"""


def clear_translation_cache():
    """Clear Django's translation cache to force reload of .mo files."""
    from django.utils.translation import trans_real
    trans_real._translations = {}
    trans_real._default = None
    trans_real._active = None

