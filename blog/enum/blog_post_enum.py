from enum import Enum
from enum import unique


@unique
class PostStatusEnum(Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"

    @classmethod
    def choices(cls) -> list:
        return [(name.value, name.name) for name in cls]
