import functools
from threading import RLock

import kombu.utils

from core.celery import celery_app

if not getattr(kombu.utils.cached_property, "lock", None):
    kombu.utils.cached_property.lock = functools.cached_property(
        lambda _: RLock()
    )
    # Must call __set_name__ here since this cached property is not defined in the context of a class
    # Refer to https://docs.python.org/3/reference/datamodel.html#object.__set_name__
    kombu.utils.cached_property.lock.__set_name__(
        kombu.utils.cached_property, "lock"
    )


__all__ = ("celery_app",)
