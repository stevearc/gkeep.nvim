import logging
import re
import typing as t
from typing import TYPE_CHECKING

from gkeepapi.node import ColorValue, TopLevelNode

if TYPE_CHECKING:
    from gkeep.api import KeepApi

logger = logging.getLogger(__name__)

FLAG_RE = re.compile(r"[+\-=]\w+", re.I)
COLOR_RE = re.compile(r"\b(?:c|colors?):([\w,]+)\b", re.I)
LABEL_RE = re.compile(r'\b(?:l|labels?):(?:"([^"]+)"|([\w,]+)\b)', re.I)
FLAG_MAP = {
    "p": "pinned",
    "a": "archived",
    "t": "trashed",
}

FlagTest = t.Callable[[bool], bool]


def flag(ftype: str) -> FlagTest:
    if ftype == "+":
        return lambda _: True
    elif ftype == "-":
        return lambda val: not val
    elif ftype == "=":
        return lambda val: val
    else:
        raise ValueError(f"Unknown flag {ftype}")


class Query:
    def __init__(self, query: str = ""):
        self._parse_errors: t.List[str] = []
        self.query = query
        self.labels: t.Optional[t.List[str]] = None
        self.colors: t.Optional[t.Set[ColorValue]] = None
        self.match_str: str = ""
        self.pinned: FlagTest = flag("+")
        self.trashed: FlagTest = flag("-")
        self.archived: FlagTest = flag("-")
        self._test: t.Optional[t.Callable[[TopLevelNode], bool]] = None
        self._parse()

    def __eq__(self, other: t.Any) -> bool:
        if other is None:
            return False
        elif isinstance(other, str):
            return self.query == other
        else:
            return self.query == other.query

    def __hash__(self) -> int:
        return hash(self.query)

    def __str__(self) -> str:
        return self.query

    def __repr__(self) -> str:
        return f"Query({self.query})"

    def _parse(self) -> None:
        query = self.query
        for flag_match in FLAG_RE.finditer(query):
            flag_str = flag_match[0]
            flag_type = flag_str[0]
            for key in flag_str[1:]:
                try:
                    attr = FLAG_MAP[key]
                except KeyError:
                    self._parse_errors.append(f"Unknown flag '{key}'")
                else:
                    setattr(self, attr, flag(flag_type))

        for color_match in COLOR_RE.finditer(query):
            for col in color_match[1].split(","):
                self._add_color(col)

        for label_match in LABEL_RE.finditer(query):
            if label_match[1]:
                self._add_label(label_match[1])
            else:
                for label in label_match[2].split(","):
                    self._add_label(label)

        query = re.sub(FLAG_RE, "", query)
        query = re.sub(COLOR_RE, "", query)
        query = re.sub(LABEL_RE, "", query)
        self.match_str = query.strip()

    @property
    def parse_errors(self) -> t.List[str]:
        return self._parse_errors

    def _add_label(self, label: str) -> None:
        if self.labels is None:
            self.labels = []
        self.labels.append(label.lower())

    def _add_color(self, color: t.Union["ColorValue", str]) -> None:
        if isinstance(color, str):
            color = ColorValue(color.upper())
        if self.colors is None:
            self.colors = set()
        self.colors.add(color)

    def compile(self, keep: "KeepApi") -> t.Callable[["TopLevelNode"], bool]:
        labels = None
        if self.labels:
            labels = set()
            for name in self.labels:
                label = keep.find_unique_label(name)
                if label is not None:
                    labels.add(label.id)
                else:
                    self._parse_errors.append(f"Unknown label '{name}'")

        search_re = None
        if self.match_str:
            # Collapse whitespace
            pattern = re.sub(r"\s+", r" ", self.match_str)
            # Escape regex patterns
            pattern = re.escape(pattern)
            # Convert space (which was turned into '\\ ' by re.escape) into \s+,
            # which will search for any amount of whitespace
            pattern = re.sub(r"\\ ", r"\\s+", pattern)
            search_re = re.compile(pattern, re.I)

        def test(node: "TopLevelNode") -> bool:
            if labels is not None:
                for label in node.labels.all():
                    if label.id in labels:
                        break
                else:
                    return False
            if self.colors is not None and node.color not in self.colors:
                return False
            if not self.pinned(node.pinned):
                return False
            if not self.trashed(node.trashed):
                return False
            if not self.archived(node.archived):
                return False
            if search_re is not None:
                return bool(search_re.search(node.title) or search_re.search(node.text))
            return True

        self._test = test
        return test

    def match(self, keep: "KeepApi", node: "TopLevelNode") -> bool:
        if self._test is None:
            test = self.compile(keep)
            return test(node)
        return self._test(node)
