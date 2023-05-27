import enum
import logging
import typing as t
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pynvim.api import Buffer, Nvim, Window

if TYPE_CHECKING:
    from pynvim.api.window import TWinBorder, TWinRelative

logger = logging.getLogger(__name__)


class Anchor(enum.Enum):
    NE = "NE"
    NW = "NW"
    SE = "SE"
    SW = "SW"


class Align(enum.Enum):
    N = "N"
    NE = "NE"
    E = "E"
    SE = "SE"
    S = "S"
    SW = "SW"
    W = "W"
    NW = "NW"
    CENTER = "CENTER"


class TextAlign(enum.Enum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    CENTER = "CENTER"


def align_text(text: str, width: int, align: TextAlign, padding: int = 0) -> str:
    if padding:
        width -= 2 * padding
    if align == TextAlign.LEFT:
        text = text.ljust(width)
    elif align == TextAlign.CENTER:
        text = text.center(width)
    elif align == TextAlign.RIGHT:
        text = text.rjust(width)
    else:
        raise ValueError(f"Unknown TextAlign value {align}")
    if padding > 0:
        padstr = padding * " "
        return padstr + text + padstr
    else:
        return text


def get_parent_dim(
    vim: Nvim, relative: str, win: t.Optional[Window]
) -> t.Tuple[int, int]:
    if relative == "editor":
        total_width = vim.options["columns"]
        total_height = vim.options["lines"] - vim.options["cmdheight"]
    else:
        if win is None:
            win = vim.current.window
        total_width = win.width
        total_height = win.height
    return (total_width, total_height)


def calc_dim(
    vim: Nvim,
    width: t.Union[int, float],
    height: t.Union[int, float],
    relative: str,
    win: t.Optional[Window],
) -> t.Tuple[int, int, int, int]:
    total_width, total_height = get_parent_dim(vim, relative, win)
    if isinstance(width, float):
        width = int(round(width * (total_width - 2)))
    if isinstance(height, float):
        height = int(round(height * (total_height - 2)))
    return width, height, total_width, total_height


def open_win(
    vim: Nvim,
    bufnr: Buffer,
    enter: bool = True,
    relative: "TWinRelative" = "win",
    row: int = 0,
    col: int = 0,
    anchor: Anchor = Anchor.NW,
    width: t.Union[int, float] = 0.9,
    height: t.Union[int, float] = 0.9,
    win: t.Optional[Window] = None,
    border: "TWinBorder" = "rounded",
    align: t.Optional[Align] = None,
    scrollable: bool = False,
) -> Window:
    width, height, total_width, total_height = calc_dim(
        vim, width, height, relative, win
    )
    if align is not None:
        if relative == "cursor":
            raise ValueError("Align requires relative = win/editor")
        anchor, row, col = calc_alignment(
            align, width, height, total_width, total_height
        )

    window = vim.api.open_win(
        bufnr,
        enter,
        {
            "relative": relative,
            "row": row,
            "col": col,
            "anchor": anchor.value,
            "width": width,
            "height": height,
            "style": "minimal",
            "border": border,
        },
    )
    if not scrollable:
        window.options["scrolloff"] = 0
        window.options["sidescrolloff"] = 0
    return window


def calc_alignment(
    align: Align, width: int, height: int, total_width: int, total_height: int
) -> t.Tuple[Anchor, int, int]:
    anchor = Anchor.NW
    row = 0
    col = 0

    if align == Align.CENTER:
        row = (total_height - height) // 2
        col = (total_width - width) // 2
    elif align == Align.N:
        row = 0
        col = (total_width - width) // 2
    elif align == Align.NE:
        anchor = Anchor.NE
        row = 0
        col = total_width
    elif align == Align.E:
        anchor = Anchor.NE
        row = (total_height - height) // 2
        col = total_width
    elif align == Align.SE:
        anchor = Anchor.SE
        row = total_height
        col = total_width
    elif align == Align.S:
        anchor = Anchor.SW
        row = total_height
        col = (total_width - width) // 2
    elif align == Align.SW:
        anchor = Anchor.SW
        row = total_height
        col = 0
    elif align == Align.W:
        row = (total_height - height) // 2
        col = 0
    elif align == Align.NW:
        pass

    return (anchor, row, col)


T = t.TypeVar("T")


class Element(t.Generic[T]):
    def __init__(
        self,
        value: T,
        display: t.Optional[t.Union[str, t.List[t.Tuple[str, str]]]] = None,
    ):
        self.value = value
        if display is None:
            self._display = [(str(value), "Normal")]
        elif isinstance(display, str):
            self._display = [(display, "Normal")]
        else:
            self._display = display
        self._display_with_len: t.Optional[t.List[t.Tuple[str, str, int]]] = None

    def display(self, vim: Nvim) -> t.List[t.Tuple[str, str, int]]:
        if self._display_with_len is None:
            self._display_with_len = [
                (text, hl, vim.funcs.strlen(text)) for text, hl in self._display
            ]
        return self._display_with_len

    @property
    def raw_display(self) -> str:
        return "".join([text for text, _ in self._display])

    def strlen(self, vim: Nvim) -> int:
        return sum([strlen for _, __, strlen in self.display(vim)])

    def byte_offset(self, vim: Nvim) -> int:
        return self.strlen(vim) - sum([len(text) for text, _ in self._display])


class Direction(enum.Enum):
    UP = "up"
    RIGHT = "right"
    DOWN = "down"
    LEFT = "left"


class ILayout(ABC, t.Generic[T]):
    def __init__(self, vim: Nvim, padding: int = 2):
        self._vim = vim
        self._window: t.Optional[Window] = None
        self._buffer: t.Optional[Buffer] = None
        self._offset = 0
        self._padding = padding

    @property
    def window(self) -> Window:
        assert self._window is not None
        return self._window

    @property
    def buffer(self) -> Buffer:
        assert self._buffer is not None
        return self._buffer

    def attach(self, window: Window, buffer: Buffer, offset: int) -> None:
        self._window = window
        self._buffer = buffer
        self._offset = offset

    @abstractmethod
    def get_min_width(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def get_min_height(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def set_width(self, width: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_selected(self) -> T:
        raise NotImplementedError

    @abstractmethod
    def set_selected(self, value: t.Optional[T]) -> None:
        raise NotImplementedError

    @abstractmethod
    def move(self, direction: Direction) -> T:
        raise NotImplementedError

    @abstractmethod
    def get_lines(self) -> t.List[str]:
        raise NotImplementedError

    @abstractmethod
    def set_highlight(self) -> None:
        raise NotImplementedError


class GridLayout(ILayout, t.Generic[T]):
    def __init__(
        self,
        vim: Nvim,
        items: t.List[t.List[Element[T]]],
        curidx: int = 0,
        align: TextAlign = TextAlign.LEFT,
        **kwargs: t.Any,
    ):
        super().__init__(vim, **kwargs)
        self._items = items
        self._align = align
        self._curidx = curidx
        self._max_len = max([max([len(e.raw_display) for e in row]) for row in items])
        self._col_width = self._max_len

    @staticmethod
    def rows_from_1d(items: t.List[T], columns: int) -> t.List[t.List[T]]:
        if columns == 0:
            columns = len(items)
        return [items[i : i + columns] for i in range(0, len(items), columns)]

    @staticmethod
    def cols_from_1d(items: t.List[T], rows: int) -> t.List[t.List[T]]:
        if rows == 0:
            rows = len(items)
        ret: t.List[t.List[T]] = []
        for i, item in enumerate(items):
            idx = i % rows
            if idx >= len(ret):
                ret.append([])
            ret[idx].append(item)
        return ret

    def set_width(self, width: int) -> None:
        self._col_width = (
            (width - self._padding) // len(self._items[0])
        ) - self._padding

    def get_min_width(self) -> int:
        return (self._max_len + self._padding) * len(self._items[0]) + self._padding

    def get_min_height(self) -> int:
        return len(self._items)

    def _get_position(self) -> t.Tuple[int, int]:
        cur = self.window.cursor
        i = cur[0] - 1 - self._offset
        row = self._items[i]
        pos = self._padding
        for j in range(0, len(row)):
            item = row[j]
            pos += self._padding + self._col_width + item.byte_offset(self._vim)
            if cur[1] < pos:
                return (i, j)
        return (i, len(self._items[i]) - 1)

    def get_selected(self) -> T:
        i, j = self._get_position()
        return self._items[i][j].value

    def _get_item_position(self, value: T) -> t.Tuple[int, int]:
        for i, row in enumerate(self._items):
            for j, item in enumerate(row):
                if item.value == value:
                    return i, j
        raise IndexError(f"Could not find item {value}")

    def set_selected(self, value: t.Optional[T]) -> None:
        if value is None:
            i, j = 0, 0
        else:
            i, j = self._get_item_position(value)
        self._set_position(i, j)

    def _col_start(self, i: int, j: int) -> int:
        row = self._items[i]
        start = self._padding
        for idx in range(0, j):
            item = row[idx]
            start += self._padding + self._col_width + item.byte_offset(self._vim)
        return start

    def _col_text_start(self, i: int, j: int) -> int:
        col = self._col_start(i, j)
        item = self._items[i][j]
        if self._align == TextAlign.CENTER:
            raw_len = len(item.raw_display)
            # This weirdness is to match the python .center() logic.
            # For some reason the extra space will change sides depending on if the
            # padded string has an even or odd length.
            # 'f'.center(2) == 'f '
            # 'ff'.center(3) == ' ff'
            if raw_len % 2 == 0:
                col += round((self._col_width - raw_len) / 2)
            else:
                col += (self._col_width - raw_len) // 2
        elif self._align == TextAlign.RIGHT:
            col += self._col_width - len(item.raw_display)
        return col

    def _set_position(self, i: int, j: int) -> None:
        col = self._col_text_start(i, j)
        item = self._items[i][j]
        for idx in range(0, self._curidx):
            col += item.display(self._vim)[idx][2]
        self.window.cursor = (i + 1 + self._offset, col)

    def move(self, direction: Direction) -> T:
        i, j = self._get_position()
        if len(self._items) == 1:
            if direction == Direction.UP:
                direction = Direction.LEFT
            elif direction == Direction.DOWN:
                direction = Direction.RIGHT
        elif len(self._items[0]) == 1:
            if direction == Direction.LEFT:
                direction = Direction.UP
            elif direction == Direction.RIGHT:
                direction = Direction.DOWN

        if direction == Direction.UP:
            i = (i - 1) % len(self._items)
        elif direction == Direction.RIGHT:
            j = (j + 1) % len(self._items[0])
        elif direction == Direction.DOWN:
            i = (i + 1) % len(self._items)
        elif direction == Direction.LEFT:
            j = (j - 1) % len(self._items[0])
        self._set_position(i, j)
        return self._items[i][j].value

    def _align_item(self, item: Element[T]) -> str:
        if self._align == TextAlign.LEFT:
            return item.raw_display.ljust(self._col_width)
        elif self._align == TextAlign.RIGHT:
            return item.raw_display.rjust(self._col_width)
        elif self._align == TextAlign.CENTER:
            return item.raw_display.center(self._col_width)
        else:
            raise ValueError(f"Unknown column alignment {self._align}")

    def get_lines(self) -> t.List[str]:
        padstr = " " * self._padding
        lines = [
            padstr + padstr.join([self._align_item(item) for item in row])
            for row in self._items
        ]
        return lines

    def set_highlight(self) -> None:
        ns = self._vim.api.create_namespace("GkeepModalHL")
        highlights = []
        for i, row in enumerate(self._items):
            for j, item in enumerate(row):
                start = self._col_text_start(i, j)
                for _, hl, strlen in item.display(self._vim):
                    highlights.append((hl, i + self._offset, start, start + strlen, ns))
                    start += strlen
        self.buffer.update_highlights(ns, highlights, clear=True)
