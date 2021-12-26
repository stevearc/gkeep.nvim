import logging
import random
import typing as t
from functools import partial

import gkeep.api
import gkeep.modal
from gkeep import parser, util
from gkeep.config import Config
from gkeep.modal import Align
from gkeep.query import Query
from gkeep.util import NoteEnum, NoteFormat, NoteType, NoteUrl
from gkeep.views.noteview import NoteView
from gkeep.views.view import View
from gkeep.views.view_util import (
    NoteTypeEditor,
    get_local_changed_file,
    render_note_line,
    render_note_list,
    split_type_and_format,
)
from gkeepapi.node import ColorValue, Note
from pynvim.api import Buffer, Nvim, Window

logger = logging.getLogger(__name__)


class NoteList(View):
    def __init__(
        self,
        vim: Nvim,
        config: Config,
        api: gkeep.api.KeepApi,
        modal: gkeep.modal.Modal,
        noteview: NoteView,
    ) -> None:
        super().__init__(vim, config, api, modal, "Note list")
        self._noteview = noteview
        self._query = Query()
        self._preferred_item: t.Optional[NoteType] = None
        self._preview_item = None
        self._note_type_editor = NoteTypeEditor(vim, config, api, modal)
        self.dispatch = partial(util.dispatch, self._vim, self._config)

    def _get_shortcuts(self) -> t.List[t.Tuple[str, str, str, str]]:
        def act(e: str, a: str = "") -> str:
            return f"<CMD>call _gkeep_list_action('{e}'{a})<CR>"

        return [
            ("n", "<CR>", act("select", ", v:true"), "Open note"),
            ("n", "<C-s>", act("select", ', v:true, "split"'), "Open note in split"),
            ("n", "<C-v>", act("select", ', v:true, "vsplit"'), "Open note in vsplit"),
            ("n", "p", act("preview"), "Toggle note preview"),
            ("nv", "P", act("pin"), "Pin/unpin"),
            ("nv", "A", act("archive"), "Archive/unarchive"),
            ("nv", "D", act("delete"), "Delete/undelete"),
            ("n", "N", "<cmd>GkeepNew<CR>", "New note"),
            ("n", "ct", act("change_type"), "Change note type"),
            ("nv", "cc", act("change_color"), "Change note color"),
            ("n", "J", act("move", ", 1"), "Move note down"),
            ("n", "K", act("move", ", -1"), "Move note up"),
            ("n", "q", "<cmd>GkeepClose<CR>", "Close Gkeep windows"),
            ("n", "yy", "<cmd>GkeepYank<CR>", "Copy a document link to the note"),
            ("n", "yl", act("yank_link"), "Copy a browser link to the note"),
            ("n", "O", "<cmd>GkeepBrowse<CR>", "Open note in the browser"),
            (
                "n",
                "<c-r>",
                "<cmd>GkeepRefresh<CR>",
                "Refresh notes (discarding local changes)",
            ),
            ("n", "?", act("show_help"), "Show help"),
        ]

    @property
    def notes(self) -> t.List[NoteType]:
        return self._api.get_search(self._query)

    @property
    def query(self) -> Query:
        return self._query

    def set_query(self, query: str) -> None:
        if self._query == query:
            return
        self._query = Query(query)
        self._query.compile(self._api)
        self._api.run_search(self._query, partial(self.dispatch, "render"))
        window = self.get_win()
        if window:
            window.cursor = (1, 0)
        self.render()

    def rerun_query(self) -> None:
        self._api.run_search(self._query, partial(self.dispatch, "render"), True)
        self.render()

    def _select_note(self, note: NoteType) -> None:
        try:
            idx = self.notes.index(note)
        except ValueError:
            pass
        else:
            window = self.get_win()
            if window is not None:
                window.cursor = (idx + 1, 0)

    def _setup_buffer(self, buffer: Buffer) -> None:
        buffer.options["filetype"] = "GoogleKeepList"
        self._vim.command(
            f"au CursorMoved <buffer={buffer.number}> ++nested call _gkeep_list_action('cursor_moved')"
        )

    def open(self) -> None:
        winid = self.get_win()
        if winid is not None:
            return
        self._vim.command("noau rightbelow split")
        self._configure_win(self._vim.current.window)
        self._create_buffer()
        self.render()

    def render(self) -> None:
        bufnr = self.bufnr
        if bufnr is None:
            return

        render_note_list(self._vim, self._config, self._api, bufnr, self.notes)
        if not self.notes:
            lines = [""]
            if self._api.is_searching(self.query):
                lines.append("Searching...".center(self._config.width))
            else:
                lines.append("<No results>".center(self._config.width))
            bufnr.options["modifiable"] = True
            bufnr[:] = lines
            bufnr.options["modifiable"] = False

        if self._preferred_item is not None:
            self._select_note(self._preferred_item)
        self._update_highlight()

    def rerender_note(self, note_or_id: t.Union[NoteType, str, None]) -> None:
        if note_or_id is None:
            return
        bufnr = self.bufnr
        if bufnr is None:
            return
        if isinstance(note_or_id, str):
            note = self._api.get(note_or_id)
        else:
            note = note_or_id
        if note not in self.notes:
            if self.query.match(self._api, note) and self._api.add_search_result(
                self.query, note
            ):
                self.render()
            return
        row = self.notes.index(note)
        highlights: t.List[t.Tuple[str, int, int, int]] = []
        line = render_note_line(
            self._vim, self._config, self._api, row, note, highlights
        )
        bufnr.options["modifiable"] = True
        bufnr[row : row + 1] = [line]
        bufnr.options["modifiable"] = False
        ns = self._vim.api.create_namespace("GkeepNoteListHL")
        bufnr.update_highlights(ns, highlights, clear_start=row, clear_end=row + 1)

    def _get_selected_notes(self) -> t.Sequence[NoteType]:
        lstart = self._vim.funcs.line("v")
        lend = self._vim.current.window.cursor[0]
        if lstart > lend:
            lstart, lend = lend, lstart
        return self.notes[lstart - 1 : lend]

    def get_note_under_cursor(self) -> t.Optional[NoteType]:
        mywin = self.get_win()
        if mywin is None:
            return None
        line = mywin.cursor[0]
        idx = line - 1
        if idx >= len(self.notes):
            return None
        return self.notes[idx]

    def cmd_select(self, enter: bool = False, action: str = "edit") -> None:
        note = self.get_note_under_cursor()
        if note is not None:
            self._open_note(note, enter, action)

    def _open_note(
        self, note: NoteType, enter: bool = False, action: str = "edit"
    ) -> None:
        startwin = self._vim.current.window
        self._edit_note(note, action)
        if enter:
            local_file = get_local_changed_file(self._config, self._api, note)
            if local_file is not None:
                ext = util.get_ext(self._vim.current.buffer.name)
                filetype = self._config.ft_from_ext(ext)
                prefix = ""
                # If diffopt is the default value, assume that the user wants a vertical split
                if self._vim.options["diffopt"] == "internal,filler,closeoff":
                    prefix = "vertical "
                self._vim.command(f"{prefix}diffsplit {local_file}")
                self._vim.current.buffer.options["filetype"] = filetype
        else:
            self._vim.current.window = startwin

    def _preview_note(self, note: NoteType) -> None:
        url = NoteUrl.from_note(note)
        bufname = url.bufname(self._api, self._config, note)
        self._vim.command(f"botright vertical pedit {bufname}")
        self._update_highlight()

    def _edit_note(self, note: NoteType, action: str) -> None:
        url = NoteUrl.from_note(note)
        self._vim.command("pclose")
        winid = self.get_normal_win()
        self._vim.current.window = winid
        self._vim.command(f"{action} {url.bufname(self._api, self._config, note)}")

    def cmd_pin(self) -> None:
        for note in self._get_selected_notes():
            note.pinned = not note.pinned
        self._preferred_item = self.get_note_under_cursor()
        self._api.resort(self._query)
        self.render()
        self.dispatch("sync")

    def cmd_archive(self) -> None:
        for note in self._get_selected_notes():
            note.archived = not note.archived
            self.rerender_note(note)
        self.dispatch("sync")

    def cmd_delete(self) -> None:
        for note in self._get_selected_notes():
            if note.trashed:
                note.untrash()
            else:
                note.trash()
            self.rerender_note(note)
        self.dispatch("sync")

    def new_note(self, note_type: NoteFormat = None, title: str = None) -> None:
        if note_type is None:
            layout = self._note_type_editor.get_note_type_layout()
            self._modal.confirm.show(
                "New note",
                self._new_note_type,
                text_margin=2,
                layout=layout,
            )
        elif title is None:
            self._new_note_type(note_type)
        else:
            self._new_note(note_type, title)

    def _new_note_type(self, new_type: NoteFormat) -> None:
        icon = self._config.get_icon(new_type.value)
        self._modal.prompt.show(
            partial(self._new_note, new_type),
            prompt=icon,
            relative="editor",
            align=Align.CENTER,
            width=60,
        )

    def _new_note(self, note_type: NoteFormat, title: str) -> None:
        type, filetype = split_type_and_format(note_type)
        if type == NoteEnum.NOTE:
            note = self._api.createNote(title)
        elif type == NoteEnum.LIST:
            note = self._api.createList(title, [("", False)])
        else:
            util.echoerr(self._vim, f"Unknown note type '{type}'")
            return
        if not self.query.pinned(note.pinned):
            note.pinned = not note.pinned
        if self.query.labels:
            for name in self.query.labels:
                label = self._api.find_unique_label(name)
                if label is not None:
                    note.labels.add(label)

        if self.query.colors and len(self.query.colors) == 1:
            note.color = list(self.query.colors)[0]
        if isinstance(note, Note):
            parser.convert(note, None, filetype)
        self.rerun_query()
        self._select_note(note)
        url = NoteUrl.from_note(note)
        filepath = url.filepath(self._api, self._config, note)
        if filepath is not None:
            logger.info("Writing %s", filepath)
            with open(filepath, "w") as ofile:
                for line in parser.serialize(self._config, note, filetype):
                    ofile.write(line)
                    ofile.write("\n")
        self._open_note(note, True)
        self._noteview.render(self._vim.current.buffer, url)

    def cmd_move(self, steps: t.Union[int, str]) -> None:
        steps = int(steps)
        note = self.get_note_under_cursor()
        if note is None:
            return
        idx = self.notes.index(note)
        newidx = idx + steps
        if newidx < 0 or newidx >= len(self.notes):
            return
        delta = 1000000
        max_sort = 9999999999
        if newidx == 0:
            low = int(self.notes[newidx].sort)
            hi = max(max_sort, low + delta)
        elif newidx == len(self.notes) - 1:
            hi = int(self.notes[newidx - 1].sort)
            low = min(0, hi - delta)
        else:
            offset = 0 if steps < 0 else 1
            n_hi = self.notes[newidx - 1 + offset]
            n_low = self.notes[newidx + offset]
            hi = int(n_hi.sort)
            low = int(n_low.sort)
            if note.pinned and not n_hi.pinned:
                return
            elif not note.pinned and n_low.pinned:
                return
            elif n_hi.pinned != n_low.pinned:
                if note.pinned:
                    low = min(0, hi - delta)
                else:
                    hi = max(max_sort, low + delta)
        note.sort = random.randint(low, hi)
        self.notes.pop(idx)
        self.notes.insert(newidx, note)
        self.render()
        self._vim.current.window.cursor = (newidx + 1, 0)

    def cmd_change_type(self) -> None:
        note = self.get_note_under_cursor()
        if note is not None:
            self._note_type_editor.change_type(note, self._on_change_type)

    def _on_change_type(self, old_note: NoteType, new_note: NoteType) -> None:
        idx = self.notes.index(old_note)
        self.notes[idx] = new_note
        self.rerender_note(new_note)
        self._noteview.rerender_note(new_note.id)

    def cmd_change_color(self) -> None:
        notes = self._get_selected_notes()
        if notes:
            self._modal.choose_color(
                partial(self._change_color, notes), initial_color=notes[0].color
            )

    def _change_color(self, notes: t.Sequence[NoteType], color: ColorValue) -> None:
        for note in notes:
            note.color = color
            self.rerender_note(note)
            self.dispatch("sync")

    def update_highlight_and_preview(self, bufnr: Buffer) -> None:
        self._update_highlight()
        mywin = self.get_win()
        if mywin is not None:
            mywin.width = self._config.width

        url = parser.url_from_file(self._config, bufnr.name, bufnr)
        if url is None:
            return
        if self._vim.current.window.options["previewwindow"]:
            # If this buffer is *only* open in the preview window, set bufhidden=wipe
            if len(self._vim.funcs.win_findbuf(bufnr)) == 1:
                bufnr.vars["prev_hidden"] = bufnr.options["bufhidden"]
                bufnr.options["bufhidden"] = "wipe"
        else:
            prev_hidden = bufnr.vars.get("prev_hidden")
            if prev_hidden is not None:
                bufnr.options["bufhidden"] = prev_hidden
                del bufnr.vars["prev_hidden"]

    def _update_highlight(self) -> None:
        mywin = self.get_win()
        mybuf = self.bufnr
        if mywin is None or mybuf is None:
            return
        notes = {note.id: i for i, note in enumerate(self.notes)}
        ns = self._vim.api.create_namespace("GkeepLineHL")
        mybuf.api.clear_namespace(ns, 0, -1)
        for window in self._vim.current.tabpage.windows:
            bufnr = window.buffer
            url = parser.url_from_file(self._config, bufnr.name, bufnr)
            if url is not None and url.id in notes:
                row = notes[url.id]
                mybuf.add_highlight("QuickFixLine", row, 0, -1, ns)

    def get_normal_win(self) -> Window:
        for window in self._vim.current.tabpage.windows:
            if self.is_normal_win(window):
                return window
        self._vim.command("noau botright vsplit")
        return self._vim.current.window

    def cmd_cursor_moved(self) -> None:
        self._preferred_item = None
        if self._is_preview_open():
            self._preview_item = self.get_note_under_cursor()
            self._vim.exec_lua(
                "vim.defer_fn(function() vim.fn._gkeep_list_action('update_preview') end, 10)"
            )

    def cmd_update_preview(self) -> None:
        win = self._get_preview_win()
        if win is None:
            return
        note = self.get_note_under_cursor()
        if note is None or note != self._preview_item:
            return
        buffer = win.buffer
        url = parser.url_from_file(self._config, buffer.name, buffer)
        if url is None or url.id == note.id:
            return
        self._preview_note(note)

    def _is_preview_open(self) -> bool:
        return self._get_preview_win() is not None

    def _get_preview_win(self) -> t.Optional[Window]:
        for window in self._vim.current.tabpage.windows:
            if window.options["previewwindow"]:
                return window
        return None

    def cmd_preview(self) -> None:
        if self._is_preview_open():
            self._vim.command("pclose")
            self._update_highlight()
        else:
            note = self.get_note_under_cursor()
            if note is not None:
                self._preview_note(note)

    def cmd_yank_link(self) -> None:
        note = self.get_note_under_cursor()
        if note is None:
            return
        line = util.get_link(note)
        self._vim.funcs.setreg("+", line)
        self._vim.funcs.setreg("", line)
