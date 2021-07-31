import typing as t

import pynvim
from gkeep.modal.confirm import Confirm, ConfirmResult
from gkeep.modal.layout import Align, Anchor, Element, GridLayout, TextAlign
from gkeep.modal.prompt import Prompt
from gkeepapi.node import ColorValue

__all__ = [
    "Align",
    "Anchor",
    "Modal",
    "ConfirmResult",
    "GridLayout",
    "Element",
    "TextAlign",
]


class Modal:
    def __init__(self, vim: pynvim.Nvim):
        self._vim = vim
        self._prompt = Prompt(vim)
        self._confirm = Confirm(vim)

    @property
    def prompt(self) -> Prompt:
        return self._prompt

    @property
    def confirm(self) -> Confirm:
        return self._confirm

    def choose_color(
        self,
        callback: t.Callable[[ColorValue], None],
        initial_color: ColorValue = ColorValue.White,
    ) -> None:
        elements = [
            Element(c, [("■ ", f"GKeep{c.value}"), (c.value, "Normal")])
            for c in ColorValue
        ]
        colors = GridLayout.rows_from_1d(elements, 4)
        layout = GridLayout(self._vim, colors, curidx=1)
        self.confirm.show(None, callback, layout=layout, initial_value=initial_color)

    def action(self, mtype: str, meth: str, *args: t.Any) -> None:
        modal = getattr(self, mtype, None)
        if modal is not None:
            getattr(modal, meth)(*args)
