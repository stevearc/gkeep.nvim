import logging
import typing as t
from functools import partial

import gkeep.api
import gkeep.modal
from gkeep import parser, util
from gkeep.config import Config
from gkeep.modal import Align
from gkeep.modal.layout import open_win
from gkeep.query import Query
from gkeep.util import NoteType
from gkeep.views.noteview import NoteView
from gkeep.views.view import View
from gkeep.views.view_util import NoteTypeEditor, render_note_list
from gkeepapi.node import ColorValue
from pynvim.api import Buffer, Nvim

logger = logging.getLogger(__name__)


class NotePopup(View):
    def __init__(
        self,
        vim: Nvim,
        config: Config,
        api: gkeep.api.KeepApi,
        modal: gkeep.modal.Modal,
        noteview: NoteView,
    ) -> None:
        super().__init__(vim, config, api, modal, "Note popup")
        self._noteview = noteview
        self._query = Query()
        self._preferred_item: t.Optional[NoteType] = None
        self._preview_item = None
        self._note_type_editor = NoteTypeEditor(vim, config, api, modal)
        self.dispatch = partial(util.dispatch, self._vim, self._config)
        self.note: t.Optional[NoteType] = None

    def _get_shortcuts(self) -> t.List[t.Tuple[str, str, str, str]]:
        def act(e: str, a: str = "") -> str:
            return f"<CMD>call _gkeep_popup_action('{e}'{a})<CR>"

        return [
            ("nv", "P", act("pin"), "Pin/unpin"),
            ("nv", "A", act("archive"), "Archive/unarchive"),
            ("nv", "D", act("delete"), "Delete/undelete"),
            ("n", "ct", act("change_type"), "Change note type"),
            ("nv", "cc", act("change_color"), "Change note color"),
            ("n", "q", "<cmd>GkeepPopup<CR>", "Close popup"),
            ("n", "yy", "<cmd>GkeepYank<CR>", "Copy a document link to the note"),
            ("n", "yl", act("yank_link"), "Copy a browser link to the note"),
            ("n", "?", act("show_help"), "Show help"),
        ]

    def _setup_buffer(self, buffer: Buffer) -> None:
        buffer.options["filetype"] = "GoogleKeepPopup"
        self._vim.command("aug GkeepPopup")
        self._vim.command("au!")
        self._vim.command("aug END")
        self._vim.command("au BufEnter * call _gkeep_popup_action('on_enter_buf')")

    def toggle(self) -> None:
        winid = self.get_win()
        if winid is not None:
            self.close()
            return
        buffer = self._vim.current.buffer
        url = parser.url_from_file(self._config, buffer.name, buffer)
        if url is not None and url.id is not None:
            self.note = self._api.get(url.id)
            if self.note is None:
                return
        tmpbuf = self._vim.api.create_buf(False, True)
        open_win(
            self._vim,
            tmpbuf,
            width=1.0,
            height=1,
            win=self._vim.current.window,
            align=Align.N,
        )
        if self.bufnr is None:
            self._create_buffer()
        else:
            self._vim.current.buffer = self.bufnr
        self.render()

    def render(self) -> None:
        bufnr = self.bufnr
        if bufnr is None or self.note is None:
            return
        render_note_list(self._vim, self._config, self._api, bufnr, [self.note])

    def cmd_on_enter_buf(self) -> None:
        # If current window is not float, close the popup
        curwin = self._vim.current.window
        if curwin.api.get_config().get("relative") == "":
            self.close()

    def close(self) -> None:
        super().close()
        self.note = None
        self._vim.command("aug GkeepPopup")
        self._vim.command("au!")
        self._vim.command("aug END")

    def cmd_pin(self) -> None:
        if self.note:
            self.note.pinned = not self.note.pinned
            self.render()
            self.dispatch("sync")

    def cmd_archive(self) -> None:
        if self.note:
            self.note.archived = not self.note.archived
            self.render()
            self.dispatch("sync")

    def cmd_delete(self) -> None:
        if self.note:
            if self.note.trashed:
                self.note.untrash()
            else:
                self.note.trash()
            self.render()
            self.dispatch("sync")

    def cmd_yank_link(self) -> None:
        if self.note:
            line = util.get_link(self.note)
            self._vim.funcs.setreg("+", line)
            self._vim.funcs.setreg("", line)

    def cmd_change_type(self) -> None:
        if self.note is not None:
            self._note_type_editor.change_type(self.note, self._on_change_type)

    def _on_change_type(self, _old_note: NoteType, new_note: NoteType) -> None:
        self.note = new_note
        self.render()

    def cmd_change_color(self) -> None:
        if self.note:
            self._modal.choose_color(
                partial(self._change_color, [self.note]), initial_color=self.note.color
            )

    def _change_color(self, notes: t.Sequence[NoteType], color: ColorValue) -> None:
        for note in notes:
            note.color = color
            self.render()
            self.dispatch("sync")
