import logging
import os
import random
import typing as t
from functools import partial

import gkeep.api
import gkeep.modal
import gkeep.noteview
from gkeep import fssync, parser, util
from gkeep.config import KEEP_FT, Config
from gkeep.modal import Align, Element, GridLayout, TextAlign
from gkeep.query import Query
from gkeep.util import NoteEnum, NoteType, NoteUrl
from gkeep.view import View
from gkeepapi.node import ColorValue, List, Note
from pynvim.api import Buffer, Nvim, Window

logger = logging.getLogger(__name__)


class NoteList(View):
    def __init__(
        self,
        vim: Nvim,
        config: Config,
        api: gkeep.api.KeepApi,
        modal: gkeep.modal.Modal,
        noteview: gkeep.noteview.NoteView,
    ) -> None:
        super().__init__(vim, config, api, modal, "Note list")
        self._noteview = noteview
        self._query = Query()
        self._preferred_item: t.Optional[NoteType] = None
        self._preview_item = None
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
            ("n", "N", act("new"), "New note"),
            ("n", "ct", act("change_type"), "Change note type"),
            ("nv", "cc", act("change_color"), "Change note color"),
            ("n", "J", act("move", ", 1"), "Move note down"),
            ("n", "K", act("move", ", -1"), "Move note up"),
            ("n", "q", "<cmd>Gkeep close<CR>", "Close Gkeep windows"),
            ("n", "yy", "<cmd>Gkeep yank<CR>", "Copy a document link to the note"),
            ("n", "yl", act("yank_link"), "Copy a browser link to the note"),
            ("n", "O", "<cmd>Gkeep browse<CR>", "Open note in the browser"),
            (
                "n",
                "<c-r>",
                "<cmd>Gkeep refresh<CR>",
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
            self._vim.current.window.cursor = (idx + 1, 0)

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
        lines = []
        highlights: t.List[t.Tuple[str, int, int, int]] = []
        for i, note in enumerate(self.notes):
            line = self._render_line(i, note, highlights)
            lines.append(line)
        if not lines:
            lines.append("")
            if self._api.is_searching(self.query):
                lines.append("Searching...".center(self._config.width))
            else:
                lines.append("<No results>".center(self._config.width))
        bufnr.options["modifiable"] = True
        bufnr[:] = lines
        bufnr.options["modifiable"] = False
        ns = self._vim.api.create_namespace("GkeepNoteListHL")
        bufnr.update_highlights(ns, highlights, clear=True)

        if self._preferred_item is not None:
            self._select_note(self._preferred_item)
        self._update_highlight()

    def _render_line(
        self, row: int, note: NoteType, highlights: t.List[t.Tuple[str, int, int, int]]
    ) -> str:
        pieces = []
        col = 0
        if isinstance(note, List):
            icon = "list"
        else:
            icon = "note"
        pieces.append(self._config.get_icon(icon))
        icon_width = self._config.get_icon_width(icon)
        highlights.append((f"GKeep{note.color.value}", row, col, col + icon_width))
        col += icon_width

        local_file = self._get_local_changed_file(note)
        if local_file is not None:
            pieces.append(self._config.get_icon("diff"))
            icon_width = self._config.get_icon_width("diff")
            highlights.append(("Error", row, col, col + icon_width))
            col += icon_width

        if note.archived:
            pieces.append(self._config.get_icon("archived"))
        if note.trashed:
            pieces.append(self._config.get_icon("trashed"))
        if note.pinned:
            pieces.append(self._config.get_icon("pinned"))

        entry = note.title.strip()
        if not entry:
            entry = "<No title>"
        pieces.append(entry)
        return "".join(pieces).ljust(self._config.width)

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
        line = self._render_line(row, note, highlights)
        bufnr.options["modifiable"] = True
        bufnr[row : row + 1] = [line]
        bufnr.options["modifiable"] = False
        ns = self._vim.api.create_namespace("GkeepNoteListHL")
        bufnr.update_highlights(ns, highlights, clear_start=row, clear_end=row + 1)

    def _get_local_changed_file(self, note: NoteType) -> t.Optional[str]:
        filepath = NoteUrl.from_note(note).filepath(self._api, self._config, note)
        if filepath is not None:
            local = fssync.get_local_file(filepath)
            if os.path.exists(local):
                return local
        return None

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
        startwin = self._vim.current.window
        note = self.get_note_under_cursor()
        if note is None:
            return
        self._edit_note(note, action)
        if enter:
            local_file = self._get_local_changed_file(note)
            if local_file is not None:
                ext = os.path.splitext(self._vim.current.buffer.name)[1]
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

    def cmd_new(self) -> None:
        layout = self._get_note_type_layout()
        self._modal.confirm.show(
            "New note",
            self._new_note_type,
            text_margin=2,
            layout=layout,
        )

    def _new_note_type(self, new_type: t.Tuple[NoteEnum, str]) -> None:
        note_type, filetype = new_type
        icon = self._config.get_icon(note_type.value)
        self._modal.prompt.show(
            partial(self._new_note, note_type, filetype),
            prompt=icon,
            relative="editor",
            align=Align.CENTER,
            width=60,
        )

    def _new_note(self, type: NoteEnum, filetype: str, title: str) -> None:
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
        self.cmd_select(True)
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

    def _get_note_type_layout(self) -> GridLayout[t.Tuple[NoteEnum, str]]:
        elements = [
            Element((NoteEnum.NOTE, KEEP_FT), self._config.get_icon("note") + "Note"),
            Element((NoteEnum.LIST, KEEP_FT), self._config.get_icon("list") + "List"),
        ]
        if self._config.support_neorg:
            elements.append(
                Element(
                    (NoteEnum.NOTE, "norg"), self._config.get_icon("note") + "Neorg"
                )
            )
        return GridLayout(
            self._vim,
            [elements],
            align=TextAlign.CENTER,
        )

    def cmd_change_type(self) -> None:
        note = self.get_note_under_cursor()
        if note is None:
            return
        layout = self._get_note_type_layout()
        initial_value = (util.get_type(note), parser.get_filetype(self._config, note))
        self._modal.confirm.show(
            f"Change type of {note.title}",
            partial(self._change_type, note),
            text_margin=2,
            initial_value=initial_value,
            layout=layout,
        )

    def _change_type(self, note: NoteType, new_type: t.Tuple[NoteEnum, str]) -> None:
        note_type, filetype = new_type
        url = NoteUrl.from_note(note)
        bufname = url.bufname(self._api, self._config, note)
        old_filetype = parser.get_filetype(self._config, note)
        if note_type == NoteEnum.NOTE and not isinstance(note, Note):
            lines = []
            new_note = Note()
            for i, item in enumerate(note.items):
                lines.append(item.text)
                if i == 0:
                    new_note.append(item)
                else:
                    item.delete()
            raw = note.save(True)
            new_note.load(raw)
            new_note.text = "\n".join(lines)
            parser.convert(new_note, old_filetype, filetype)
        elif note_type == NoteEnum.LIST and not isinstance(note, List):
            parser.convert(note, old_filetype, filetype)
            lines = note.text.split("\n")
            note.text = lines.pop(0)
            raw = note.save(True)
            new_note = List()
            new_note.load(raw)
            for child in note.children:
                new_note.append(child)
            sort = int(new_note.items[0].sort)
            for line in lines:
                new_note.add(line, False, sort)
                sort -= 100000
        else:
            new_note = note
            if isinstance(new_note, Note):
                parser.convert(new_note, old_filetype, filetype)
        for label in note.labels.all():
            new_note.labels.add(label)

        idx = self.notes.index(note)
        new_note.touch()
        self.notes[idx] = new_note
        self._api.add(new_note)
        self.rerender_note(new_note)
        new_bufname = url.bufname(self._api, self._config, note)
        if bufname != new_bufname:
            self.dispatch("rename_files", {bufname: new_bufname})
        self._noteview.rerender_note(new_note.id)
        self.dispatch("sync")

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
