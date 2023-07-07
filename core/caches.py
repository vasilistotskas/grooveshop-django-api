from __future__ import annotations

from django.core.cache import BaseCache
from django.core.cache import caches

cache: BaseCache | BaseCache = caches["default"]

ONE_HOUR = 60 * 60
ONE_DAY = ONE_HOUR * 24
ONE_WEEK = ONE_DAY * 7
ONE_MONTH = ONE_DAY * 30
ONE_YEAR = ONE_DAY * 365

SESSION = "session"
USER = "user"
CLEAR_SESSIONS_FOR_NONE_USERS_TASK = "clear_sessions_for_none_users_task"


def set(key: str, data, duration: int, *args, **kwargs) -> None:
    cache.set("%s" % key.format(*args, **kwargs), data, duration)


def get(key: str, *args, **kwargs) -> BaseCache | BaseCache:
    return cache.get("%s" % key.format(*args, **kwargs))


def add(key: str, data, duration: int, *args, **kwargs) -> None:
    cache.add("%s" % key.format(*args, **kwargs), data, duration)


def delete(key: str, *args, **kwargs) -> None:
    cache.delete("%s" % key.format(*args, **kwargs))
