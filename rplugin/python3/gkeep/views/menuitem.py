import typing as t
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gkeep.config import Config


class MenuItem:
    def __init__(self, icon: str, name: str, query: str):
        self.icon = icon
        self.name = name
        self.query = query

    def title(self, config: "Config") -> str:
        return config.get_icon(self.icon) + self.name

    def __hash__(self) -> int:
        return hash(self.query)

    def __eq__(self, other: t.Any) -> bool:
        if other is None:
            return False
        return self.query == other.query
