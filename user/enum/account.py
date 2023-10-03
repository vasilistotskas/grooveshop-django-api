from __future__ import annotations

from enum import Enum
from enum import unique


@unique
class UserRole(Enum):
    SUPERUSER = "admin"
    STAFF = "staff"
    USER = "user"
    GUEST = "guest"
