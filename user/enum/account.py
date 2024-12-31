from __future__ import annotations

from enum import Enum, unique


@unique
class UserRole(Enum):
    SUPERUSER = "admin"
    STAFF = "staff"
    USER = "user"
    GUEST = "guest"
