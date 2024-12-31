from dataclasses import dataclass
from typing import Any


@dataclass
class NotificationData:
    user_id: int
    seen: bool
    link: str
    kind: str
    translations: dict[str, dict[str, Any]]
