import enum
import logging
import threading
import time
import typing as t
from functools import partial

import gkeep.api
import gkeep.modal
import gkeep.status as gstatus
from gkeep import util
from gkeep.config import Config, State
from gkeep.modal import ConfirmResult
from gkeep.views.menuitem import MenuItem
from gkeep.views.notelist import NoteList
from gkeep.views.view import View
from pynvim.api import Buffer, Nvim, Window

logger = logging.getLogger(__name__)


class Position(enum.Enum):
    LEFT = "left"
    RIGHT = "right"


class Menu(View):
    def __init__(
        self,
        vim: Nvim,
        config: Config,
        api: gkeep.api.KeepApi,
        modal: gkeep.modal.Modal,
        notelist: NoteList,
    ) -> None:
        super().__init__(vim, config, api, modal, "Menu")
        self._notelist = notelist
        self._home = MenuItem("home", "Home", "")
        self._labels: t.List[MenuItem] = []
        self._last_search: t.Optional[MenuItem] = None
        self._archived = MenuItem("archived", "Archive", "=a")
        self._trashed = MenuItem("trashed", "Trash", "=t")
        self._status_thread = threading.Thread(
            target=self._run_update_status, daemon=True
        )
        self._status_thread.start()
        self._last_status: t.Optional[str] = None
        self._ns_id = None
        self.dispatch = partial(util.dispatch, self._vim, self._config)

    def _get_shortcuts(self) -> t.List[t.Tuple[str, str, str, str]]:
        def act(e: str, a: str = "") -> str:
            return f"<CMD>call _gkeep_menu_action('{e}'{a})<CR>"

        return [
            ("n", "<CR>", act("select", ", v:true"), "Jump to note list"),
            ("n", "/", act("search"), "Search"),
            ("n", "S", act("save"), "Save last search"),
            ("n", "R", act("rename"), "Rename search or label"),
            ("n", "E", act("edit"), "Edit search"),
            ("n", "D", act("delete"), "Delete search or label"),
            ("n", "N", act("new_label"), "New label"),
            ("n", "q", "<cmd>GkeepClose<CR>", "Close Gkeep windows"),
            (
                "n",
                "<c-r>",
                "<cmd>GkeepRefresh<CR>",
                "Refresh notes (discarding local changes)",
            ),
            ("n", "?", act("show_help"), "Show help"),
        ]

    def _run_update_status(self) -> None:
        while self._config.state != State.ShuttingDown:
            time.sleep(1 / gstatus.default_spinner.fps)
            status = gstatus.get_status(True)
            if status != self._last_status:
                self.dispatch("render_status")

    def _get_item_under_cursor(self) -> t.Union[MenuItem, None]:
        mywin = self.get_win()
        if mywin is None:
            return None
        line = mywin.cursor[0]
        idx = line - 1
        if idx >= len(list(self.items)):
            return None
        return list(self.items)[idx]

    def refresh(self, force: bool = False) -> None:
        current_item = self._get_item_under_cursor()
        self._labels.clear()
        for label in self._api.labels():
            self._labels.append(MenuItem("label", label.name, f'l:"{label.name}"'))
        self.render()
        if current_item != self._get_item_under_cursor():
            self.cmd_select(False)
        if force or not self._notelist.notes:
            self._notelist.rerun_query()

    @property
    def items(self) -> t.Iterator[MenuItem]:
        yield self._home
        yield from self._labels
        yield from self._config.saved_searches
        if self._last_search is not None:
            yield self._last_search
        yield self._archived
        yield self._trashed

    def close(self) -> None:
        super().close()
        self._notelist.close()

    def _setup_win(self, window: Window) -> None:
        window.options["winfixheight"] = True

    def open(self, enter: bool = True, position: Position = Position.LEFT) -> None:
        winid = self.get_win()
        if winid is not None:
            if enter:
                self._vim.current.window = winid
            return
        startwin = self._vim.current.window
        if position == Position.RIGHT:
            self._vim.command("noau vertical botright split")
        else:
            self._vim.command("noau vertical topleft split")
        self._configure_win(self._vim.current.window)

        if self.bufnr is None:
            self._create_buffer()
        else:
            self._vim.current.buffer = self.bufnr

        mywin = self._vim.current.window
        self.render()
        # Reselect previously-selected menu item, if exists
        lnum = 1
        for i, item in enumerate(self.items):
            if item.query == self._notelist.query:
                lnum = i + 1
                break
        mywin.api.set_cursor((lnum, 0))
        self.cmd_select()
        self._notelist.open()
        mywin.height = 8
        if enter:
            self._vim.current.window = mywin
        else:
            self._vim.current.window = startwin

    def _setup_buffer(self, buffer: Buffer) -> None:
        buffer.options["filetype"] = "GoogleKeepMenu"
        self._vim.command(
            f"au CursorMoved <buffer={buffer.number}> call _gkeep_menu_action('select')"
        )

    def toggle(self, enter: bool = True, position: Position = Position.LEFT) -> None:
        if self.is_visible:
            self.close()
        else:
            self.open(enter, position)

    def render(self, jump_to_lnum: int = None, num_lines: int = -1) -> None:
        bufnr = self.bufnr
        if bufnr is None:
            return

        lines = [item.title(self._config) for item in self.items]
        if num_lines > 0:
            lines = lines[:num_lines]
        status = self._last_status = gstatus.get_status("right")
        if status is None and not self._api.is_logged_in:
            status = "Log in with :GkeepLogin"

        highlights = []
        if status and lines:
            statuslen = self._vim.funcs.strdisplaywidth(status)
            linelen = self._vim.funcs.strdisplaywidth(lines[0])
            start = self._vim.funcs.strlen(lines[0])
            lines[0] = (
                lines[0] + " " * (self._config.width - (statuslen + linelen)) + status
            )
            highlights.append(("GkeepStatus", 0, start, -1))

        bufnr.options["modifiable"] = True
        if num_lines == -1:
            bufnr[:] = lines
        else:
            bufnr[0:num_lines] = lines
        bufnr.options["modifiable"] = False

        ns = self._vim.api.create_namespace("GkeepMenuHL")
        bufnr.api.clear_namespace(ns, 0, -1)
        bufnr.update_highlights(ns, highlights, clear=True)
        if jump_to_lnum is not None:
            for winid in self._vim.funcs.win_findbuf(bufnr):
                self._vim.api.win_set_cursor(winid, (jump_to_lnum, 0))
        self._show_search_virtual_text()

    def render_status(self) -> None:
        self.render(num_lines=1)

    def _show_search_virtual_text(self) -> None:
        bufnr = self.bufnr
        if bufnr is None:
            return
        ns = self._vim.api.create_namespace("GkeepMenuVT")
        bufnr.api.clear_namespace(ns, 0, -1)
        item = self._get_item_under_cursor()
        if item is None or item.icon != "search":
            return
        idx = list(self.items).index(item)
        vtext: t.List[t.Tuple[str, str]] = [(item.query, "Comment")]
        if self._notelist.query.parse_errors:
            text = ", ".join(self._notelist.query.parse_errors) + " "
            vtext.insert(0, (text, "Error"))
        bufnr.api.set_virtual_text(ns, idx, vtext, {})

    def cmd_select(self, enter: t.Union[int, bool] = False) -> None:
        startwin = self._vim.current.window
        item = self._get_item_under_cursor()
        if item is None:
            return
        self._notelist.set_query(item.query)

        winid = self._notelist.get_win()
        if enter and winid:
            self._vim.current.window = winid
        else:
            self._vim.current.window = startwin
            self._show_search_virtual_text()

    def cmd_search(self) -> None:
        self._modal.prompt.show(
            self._search,
            on_cancel=self.cmd_select,
            on_change=self._on_search,
            prompt=self._config.get_icon("search"),
            width=1.0,
        )

    def _search(self, query: str) -> None:
        if self._last_search is not None:
            self._last_search.query = query
        else:
            self._last_search = MenuItem("search", "Last Search", query)

        lnum = list(self.items).index(self._last_search) + 1
        self.render(lnum)
        self._notelist.set_query(query)

    def _on_search(self, query: str) -> None:
        self._notelist.set_query(query)

    def cmd_save(self) -> None:
        item = self._last_search
        if item is None:
            return
        existing = None
        if item.icon == "search" and item != self._last_search:
            existing = item.name
        self._modal.prompt.show(self._save, text=existing, width=1.0)

    def _save(self, name: str) -> None:
        item = self._last_search
        if item is None:
            return
        if item == self._last_search:
            new_item = MenuItem("search", name, item.query)
            self._config.add_saved_search(new_item)
            self._last_search = None
        else:
            item.name = name
            self._config.save_cache()
            new_item = item

        lnum = list(self.items).index(new_item) + 1
        self.render(lnum)

    def cmd_rename(self) -> None:
        item = self._get_item_under_cursor()
        if item is None:
            return
        elif item.icon == self._last_search:
            self.cmd_save()
        elif item.icon == "label":
            self._rename_label(item)
        elif item.icon == "search":
            self._rename_search(item)
        else:
            util.echoerr(self._vim, "Can only rename labels and searches")

    def _rename_search(self, item: MenuItem, name: str = None) -> None:
        if name is None:
            self._modal.prompt.show(
                partial(self._rename_search, item), text=item.name, width=1.0
            )
        else:
            item.name = name
            self._config.save_cache()
            self.render()
            self.dispatch("sync")

    def _rename_label(self, item: MenuItem, name: str = None) -> None:
        if self._config.sync_dir is not None:
            util.echoerr(
                self._vim, "Renaming labels is not supported with g:gkeep_sync_dir set"
            )
            return
        if name is None:
            self._modal.prompt.show(
                partial(self._rename_label, item), text=item.name, width=1.0
            )
        else:
            label = self._api.findLabel(item.name)
            if label is not None:
                label.name = name
                self.refresh()
                self.dispatch("sync")

    def cmd_delete(self, force: bool = None, item: t.Optional[MenuItem] = None) -> None:
        if item is None:
            item = self._get_item_under_cursor()
        if item is None:
            return
        elif item.icon == "search":
            self._config.remove_saved_search(item)
        elif item.icon == "label":
            if force is None:
                return self._modal.confirm.show(
                    f"Are you sure you would like to delete label '{item.name}'? This cannot be undone.",
                    lambda ret: self.cmd_delete(ret == ConfirmResult.YES, item),
                    initial_value=ConfirmResult.NO,
                )
            elif force:
                label = self._api.findLabel(item.name)
                if label is not None:
                    self._api.deleteLabel(label.id)
                    self._labels.remove(item)
                    self.dispatch("sync")
        else:
            return

        self.render()

    def cmd_new_label(self) -> None:
        self._modal.prompt.show(
            self._new_label,
            prompt=self._config.get_icon("label"),
            width=1.0,
        )

    def _new_label(self, name: str) -> None:
        label = self._api.createLabel(name)
        new_item = MenuItem("label", label.name, f'l:"{label.name}"')
        self._labels.append(new_item)
        lnum = list(self.items).index(new_item) + 1
        self.render(lnum)
        self.dispatch("sync")

    def cmd_edit(self) -> None:
        item = self._get_item_under_cursor()
        if item is None:
            return
        if item.icon == "search":
            self._edit_search(item)
        elif item.icon == "label":
            self.cmd_rename()
        else:
            util.echoerr(self._vim, "Can only edit labels and searches")

    def _edit_search(self, item: MenuItem) -> None:
        self._modal.prompt.show(
            partial(self._do_edit_search, item),
            on_cancel=self.cmd_select,
            on_change=self._on_search,
            prompt=self._config.get_icon("search"),
            text=item.query,
            width=1.0,
        )

    def _do_edit_search(self, item: MenuItem, query: str) -> None:
        lnum = list(self.items).index(item) + 1
        item.query = query
        self.render(lnum)
        self._notelist.set_query(query)
        self._config.save_cache()
        self._show_search_virtual_text()
