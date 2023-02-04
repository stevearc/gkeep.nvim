import enum
import logging
import textwrap
import typing as t
from typing import TYPE_CHECKING

from gkeep.modal.layout import (
    Align,
    Direction,
    Element,
    GridLayout,
    ILayout,
    TextAlign,
    align_text,
    calc_dim,
    get_parent_dim,
    open_win,
)
from pynvim.api import Buffer, Nvim, Window

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pynvim.api.window import TWinRelative


class ConfirmResult(enum.IntEnum):
    NO = 0
    YES = 1


T = t.TypeVar("T")


class Confirm:
    def __init__(self, vim: Nvim):
        self._vim = vim
        self._winid: t.Optional[Window] = None
        self._layout: t.Optional[ILayout[t.Any]] = None
        self._callback: t.Optional[t.Callable[[t.Any], None]] = None
        self._on_cancel: t.Optional[t.Callable[[], None]] = None
        self._default_layout = GridLayout(
            vim,
            [[Element(ConfirmResult.NO, "No"), Element(ConfirmResult.YES, "Yes")]],
            align=TextAlign.CENTER,
        )

    def _create_buffer(self, cancel_keys: t.Optional[t.List[str]]) -> Buffer:
        bufnr = self._vim.api.create_buf(False, True)
        bufnr.options["buftype"] = "nofile"
        bufnr.options["bufhidden"] = "wipe"
        bufnr.options["swapfile"] = False

        def keymap(lhs: str, rhs: str) -> None:
            bufnr.api.set_keymap("n", lhs, rhs, {"silent": True, "noremap": True})

        cancel_keys = cancel_keys or []
        cancel_keys.extend(["<C-c>", "q", "<Esc>"])
        for key in set(cancel_keys):
            keymap(key, "<cmd>call _gkeep_modal('confirm', 'cancel')<CR>")

        keymap("<CR>", "<cmd>call _gkeep_modal('confirm', 'select')<CR>")
        for key in ["h", "b", "<left>"]:
            keymap(key, "<cmd>call _gkeep_modal('confirm', 'move', 'left')<CR>")
        for key in ["l", "w", "<right>"]:
            keymap(key, "<cmd>call _gkeep_modal('confirm', 'move', 'right')<CR>")
        for key in ["k", "<up>"]:
            keymap(key, "<cmd>call _gkeep_modal('confirm', 'move', 'up')<CR>")
        for key in ["j", "<down>"]:
            keymap(key, "<cmd>call _gkeep_modal('confirm', 'move', 'down')<CR>")
        self._vim.command(
            f"au BufLeave,BufHidden <buffer={bufnr.number}> ++once ++nested call _gkeep_modal('confirm', 'cancel')"
        )
        return bufnr

    def show(
        self,
        text: t.Optional[t.Union[str, t.List[str]]],
        callback: t.Callable[[T], None],
        on_cancel: t.Optional[t.Callable[[], None]] = None,
        align: Align = Align.CENTER,
        text_align: TextAlign = TextAlign.CENTER,
        layout: t.Optional[ILayout[T]] = None,
        relative: "TWinRelative" = "editor",
        width: t.Optional[t.Union[int, float]] = None,
        height: t.Optional[t.Union[int, float]] = None,
        text_margin: t.Optional[int] = None,
        max_width: int = 120,
        min_width: int = 20,
        initial_value: t.Optional[T] = None,
        win: t.Optional[Window] = None,
        cancel_keys: t.Optional[t.List[str]] = None,
        **kwargs: t.Any,
    ) -> None:
        if self._winid is not None and self._vim.api.win_is_valid(self._winid):
            return
        if layout is None:
            layout = self._default_layout
        self._layout = layout
        self._callback = callback
        self._on_cancel = on_cancel
        bufnr = self._create_buffer(cancel_keys)

        padding = 2
        if isinstance(text, str) and text:
            text = [text]
        if text and text_margin is None:
            text_margin = 4
        total_width, total_height = get_parent_dim(self._vim, relative, win)
        max_width = min(max_width, total_width - padding)
        if width is None:
            width = min_width
            if text:
                width = 2 * padding + max((len(l) for l in text))
                if width > max_width:
                    text = textwrap.wrap(" ".join(text), max_width - 2 * padding)
                    width = max_width
            width = max(width, layout.get_min_width())
        lines: t.List[str] = []
        if text:
            lines.extend(text)
        if height is None:
            height = len(lines) + layout.get_min_height()
            if text_margin is not None:
                height = max(height, min(total_height, height + text_margin))
        assert width is not None
        width, height, _, __ = calc_dim(self._vim, width, height, relative, win)
        for i, line in enumerate(lines):
            lines[i] = align_text(line, width, text_align, padding)
        lines.extend((height - len(lines) - layout.get_min_height()) * [""])
        offset = len(lines)
        layout.set_width(width)
        lines.extend(layout.get_lines())
        self._winid = open_win(
            self._vim,
            bufnr,
            align=align,
            relative=relative,
            win=win,
            width=width,
            height=height,
            **kwargs,
        )
        assert self._winid is not None
        self._layout.attach(self._winid, bufnr, offset)
        self._vim.api.buf_set_lines(bufnr, 0, 1, True, lines)
        self._vim.api.win_set_cursor(self._winid, [len(lines), 2])
        self._vim.api.buf_set_option(bufnr, "modifiable", False)
        t.cast(ILayout[T], self._layout).set_selected(initial_value)
        self._layout.set_highlight()

    def move(self, direction: str) -> None:
        if self._layout is not None:
            self._layout.move(Direction(direction))

    def cancel(self) -> None:
        on_cancel = self._on_cancel
        self._callback = None
        self._on_cancel = None
        if self._winid is not None and self._vim.api.win_is_valid(self._winid):
            self._winid.api.close(True)
        self._winid = None
        if on_cancel is not None:
            on_cancel()

    def select(self) -> None:
        callback = self._callback
        layout = self._layout
        self._callback = None
        self._callback = None
        self._on_cancel = None
        self._layout = None
        if self._winid is None or not self._vim.api.win_is_valid(self._winid):
            return
        result = None
        if layout is not None:
            result = layout.get_selected()
        self._winid.api.close(True)
        self._winid = None
        if callback is not None:
            callback(result)
