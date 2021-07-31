import inspect
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import time
import typing as t
from functools import partial, wraps

import gkeep.fssync as fssync
import gkeep.menu
import gkeep.modal
import gkeep.notelist
import gkeep.noteview
import keyring
import pynvim
from gkeep import parser, util
from gkeep.api import KeepApi
from gkeep.config import Config, State
from gkeep.menu import Position
from gkeep.modal import Align, ConfirmResult
from gkeep.parser import ALLOWED_EXT, keep
from gkeep.query import Query
from gkeep.status import get_status, status
from gkeep.thread_util import background
from gkeep.util import NoteUrl, get_type
from gkeepapi.node import List, TopLevelNode

if sys.version_info < (3, 8):
    from typing_extensions import TypedDict
else:
    from typing import TypedDict

logger = logging.getLogger(__name__)


F = t.TypeVar("F", bound=t.Callable[..., t.Any])


def unwrap_args(f: F) -> F:
    @wraps(f)
    def d(self: t.Any, *args: t.Any) -> t.Any:
        if len(args) == 1 and isinstance(args[0], list):
            return f(self, *args[0])
        return f(self, *args)

    return d  # type: ignore[return-value]


NOTES_GLOB = ",".join(["*" + ext for ext in ALLOWED_EXT])

A = t.TypeVar("A", bound=t.Callable[..., None])


class NoteSummary(TypedDict):
    id: str
    icon: str
    color: str
    title: str
    filename: str


def require_state(
    *states: State, inv: t.Container[State] = (), log: bool = False
) -> t.Callable[[A], A]:
    def d(f: A) -> A:
        def w(self: "GkeepPlugin", *args: t.Any, **kwargs: t.Any) -> None:
            state = self._config.state
            if (states and state not in states) or (inv and state in inv):
                logger.debug(
                    "Ignoring method %s. State %s not allowed", f.__name__, state
                )
                if log:
                    self._vim.err_write(
                        f"Cannot perform action while Gkeep is {state.name}\n"
                    )
            else:
                f(self, *args, **kwargs)

        return w  # type: ignore[return-value]

    return d


@pynvim.plugin
class GkeepPlugin:
    def __init__(self, vim: pynvim.Nvim) -> None:
        self._vim = vim
        self._protected_files: t.Set[str] = set()
        cache_dir = vim.funcs.stdpath("cache")
        logfile = os.path.join(cache_dir, "gkeep.log")
        handler = logging.handlers.RotatingFileHandler(
            logfile, delay=True, backupCount=1, maxBytes=1024 * 1024
        )
        formatter = logging.Formatter(
            "%(levelname)s %(asctime)s [%(name)s] %(message)s"
        )
        handler.setFormatter(formatter)
        log = logging.getLogger("gkeep")
        log.setLevel(logging.ERROR)
        log.addHandler(handler)
        log = logging.getLogger("gkeepapi")
        log.setLevel(logging.ERROR)
        log.addHandler(handler)
        self._config = Config.from_vim(vim)
        for logname, level in self._config.log_levels.items():
            logging.getLogger(logname).setLevel(level)
        self._api = KeepApi()
        self._modal = gkeep.modal.Modal(vim)
        self._noteview = gkeep.noteview.NoteView(vim, self._config, self._api)
        self._notelist = gkeep.notelist.NoteList(
            vim, self._config, self._api, self._modal, self._noteview
        )
        self._menu = gkeep.menu.Menu(
            vim, self._config, self._api, self._modal, self._notelist
        )
        if self._config.sync_dir:
            vim.command("au BufNew *.keep call _gkeep_set_note_type(expand('<abuf>'))")
            vim.command(
                f"au BufWritePost {NOTES_GLOB} call _gkeep_sync_file(expand('<abuf>'))"
            )
        if self._config.support_neorg:
            vim.command(
                f"au BufWritePre {NOTES_GLOB} call _gkeep_buf_write_pre(expand('<abuf>'))"
            )
        self.dispatch = partial(util.dispatch, self._vim, self._config)
        # The first exec_lua call takes a while in menu.render, but for some reason
        # calling it here is very fast and prevents the delay later.
        self.dispatch("refresh")

    @pynvim.shutdown_hook
    def on_shutdown(self) -> None:
        self._config.state = State.ShuttingDown

    @background(max_waiting=0)
    def sync_bg_thread(self) -> None:
        while self._config.state != State.ShuttingDown:
            time.sleep(10)
            self.dispatch("sync")

    @pynvim.command(
        "Gkeep", nargs="*", complete="customlist,_gkeep_command_complete", sync=True
    )
    @unwrap_args
    def keep_command(self, cmd: str = "login", *args: t.Any) -> None:
        meth = f"cmd_{cmd}"
        if hasattr(self, meth):
            getattr(self, meth)(*args)
        else:
            self._vim.err_write(f"Unknown Gkeep command '{cmd}'\n")

    @pynvim.function("_gkeep_health", sync=True)
    @unwrap_args
    def health_report(self) -> t.Dict[str, t.Any]:
        return {
            "logged_in": self._api.is_logged_in,
            "email": self._api.getEmail(),
            "support_neorg": self._config.support_neorg,
            "sync_dir": self._config.sync_dir,
        }

    @pynvim.function("GkeepStatus", sync=True)
    @unwrap_args
    def get_status_str(self) -> str:
        st = get_status()
        return "" if st is None else st

    @pynvim.function("_gkeep_command_complete", sync=True)
    @unwrap_args
    def keep_command_complete(
        self, arg_lead: str, _line: str, _cursor_pos: int
    ) -> t.List[str]:
        ret = []
        for name, _ in inspect.getmembers(self, predicate=inspect.ismethod):
            if name.startswith("cmd_"):
                cmd = name[4:]
                if cmd.startswith(arg_lead):
                    ret.append(cmd)
        return ret

    @pynvim.function("_gkeep_preload")
    @unwrap_args
    @background(max_waiting=0)
    def preload(self) -> None:
        email = self._config.email
        if email is not None and not self._api.is_logged_in:
            token = keyring.get_password("google-keep-token", email)
            if token:
                state = self._config.load_state()
                self._resume(email, token, state)

    @pynvim.function("_gkeep_dispatch", sync=True)
    @unwrap_args
    def dispatch_event(self, event: str, *args: t.Any) -> None:
        meth = f"event_{event}"
        if hasattr(self, meth):
            getattr(self, meth)(*args)
        else:
            self._vim.err_write(f"Unknown Gkeep event '{event}'\n")

    @background
    @require_state(State.Uninitialized)
    def _resume(self, email: str, token: str, state: t.Optional[t.Any]) -> None:
        logger.info("Resuming gkeep session for %s", email)
        with status("Loading cache"):
            self._api.resume(email, token, state=state, sync=False)
        if not self._api.is_logged_in:
            return
        self._config.state = State.InitialSync
        if state is None:
            for filename, _ in fssync.find_files(self._config):
                self._protected_files.add(filename)
        else:
            with status("Reading notes from disk"):
                self._protected_files.update(
                    fssync.find_files_with_changes(self._api, self._config)
                )
                # Clear the dirty state from reading in files.
                # We want this first sync to simply update our internal state, and
                # *then* we will process any changes in the note files.
                self._api.resume(email, token, state=state, sync=False)
        self.dispatch("refresh")
        self._api.sync(partial(self.dispatch, "finish_sync"), True)

    @require_state(State.Running)
    def event_sync(self, resync: bool = False, force: bool = False) -> None:
        # We may have opened vim on a note file. If so, we will need to set the
        # note_type once the api has loaded
        self._set_note_type(self._vim.current.buffer.number)

        if not force and not resync and not self._api.is_dirty:
            return
        logger.debug("Syncing gkeep %s", "(force refresh)" if resync else "")
        self._api.sync(partial(self.dispatch, "finish_sync"), resync)

    @require_state(State.InitialSync, State.Running)
    def event_finish_sync(
        self,
        resync: bool,
        keep_version: str,
        user_info: t.Any,
        nodes: t.Sequence[t.Any],
    ) -> None:
        # If any notes/labels were changed during sync, process & queue those changes
        self.event_sync()
        updated_notes = self._api.apply_updates(resync, keep_version, user_info, nodes)
        self.event_refresh(resync)
        self._config.save_state(self._api.dump())
        logger.debug("Sync complete. Updated %d notes", len(updated_notes))

        if self._config.sync_dir:
            notes = [
                fssync.NoteFile.from_note(self._api, self._config, n)
                for n in self._api.all()
            ]
            self._write_files(notes, updated_notes)
        if self._config.state == State.InitialSync:
            self._load_new_files()

    @background
    @status("Syncing new notes")
    @require_state(State.InitialSync)
    def _load_new_files(self) -> None:
        renames = {}
        for filename, new_filename in fssync.load_new_files(self._api, self._config):
            renames[filename] = new_filename
        self._config.state = State.Running
        if renames:
            self.dispatch("rename_files", renames)
        self.dispatch("refresh", True)
        self.dispatch("sync")
        self.sync_bg_thread()

    @require_state(State.InitialSync, State.Running)
    @background
    def _write_files(
        self,
        notes: t.Sequence[fssync.NoteFile],
        updated_notes: t.Set[str],
    ) -> None:
        renamed_files = fssync.write_files(
            self._api, self._config, self._protected_files, notes, updated_notes
        )
        if renamed_files:
            self.dispatch("rename_files", renamed_files)
        self._protected_files.clear()

    @require_state(State.InitialSync, State.Running)
    def event_refresh(self, force: bool = False) -> None:
        self._menu.refresh(force)

    @require_state(inv=[State.ShuttingDown])
    def event_render(self) -> None:
        self._menu.render()
        self._notelist.render()

    @require_state(inv=[State.ShuttingDown])
    def event_render_status(self) -> None:
        self._menu.render_status()

    @pynvim.function("_gkeep_list_action", sync=True)
    @unwrap_args
    @require_state(State.Running)
    def list_action(self, action: str, *args: t.Any) -> None:
        self._notelist.action(action, *args)

    @pynvim.function("_gkeep_menu_action", sync=True)
    @unwrap_args
    @require_state(inv=[State.ShuttingDown])
    def menu_action(self, action: str, *args: t.Any) -> None:
        self._menu.action(action, *args)

    @pynvim.function("_gkeep_modal", sync=True)
    @unwrap_args
    @require_state(inv=[State.ShuttingDown])
    def modal_action(self, mtype: str, meth: str, *args: t.Any) -> None:
        self._modal.action(mtype, meth, *args)

    @pynvim.function("_gkeep_prompt_close", sync=True)
    @unwrap_args
    @require_state(inv=[State.ShuttingDown])
    def close_prompt(self, text: str = None) -> None:
        self._modal.prompt.close(text)

    @pynvim.function("_gkeep_omnifunc", sync=True)
    @unwrap_args
    def omnifunc(self, findstart: int, base: str) -> t.Union[int, t.List[str]]:
        lnum, col = self._vim.current.window.cursor
        line = self._vim.current.buffer[lnum - 1]
        if findstart:
            # We only care about completing on the labels line
            if lnum > 3:
                return -3
            i = col - 1
            if line[i] in [",", " "]:
                return -1
            while i > 0 and not line[i - 1] in [",", " "]:
                i -= 1
            return max(i, 0)
        else:
            if col <= len("labels:"):
                candidates = ["labels: "]
            elif line.startswith("labels:"):
                candidates = [l.name for l in self._api.labels()]
            else:
                return []
            if not base:
                return candidates
            return [c for c in candidates if c.startswith(base)]

    @pynvim.autocmd("BufReadCmd", "gkeep://*", eval='expand("<amatch>")', sync=True)
    @require_state(State.Running, log=True)
    def _load_note(self, address: str) -> None:
        bufnr = self._vim.current.buffer
        url = NoteUrl.from_ephemeral_bufname(address)
        self._vim.command(f"silent doau BufReadPre {url}")
        # Undo should not return to a blank buffer
        # Method taken from :h clear-undo
        level = bufnr.options["undolevels"]
        bufnr.options["undolevels"] = -1
        self._noteview.render(bufnr, url)
        # Not sure how this could happen, but the undolevels were so high that it was
        # crashing when we tried to set it back
        if level > 100000:
            level = 100000
        bufnr.options["undolevels"] = level

        self._vim.command(f"silent doau BufReadPost {url}")
        ext = os.path.splitext(bufnr.name)[1][1:]
        self._vim.exec_lua("require('gkeep').on_ephemeral_buf_read(...)", ext)

    @pynvim.autocmd("BufWriteCmd", "gkeep://*", eval='expand("<abuf>")', sync=True)
    @require_state(State.Running, log=True)
    def _save_note(self, bufnrstr: str) -> None:
        bufnr = self._vim.buffers[int(bufnrstr)]
        url = parser.url_from_file(self._config, bufnr.name, bufnr)
        if not url:
            self._vim.err_write(f"Buffer {bufnrstr} has malformed Gkeep bufname\n")
            return
        self._noteview.save_buffer(bufnr)
        self._notelist.rerender_note(url.id)
        self._vim.command(f"silent doau BufWritePre {url}")
        bufnr.options["modified"] = False
        self._vim.command(f"silent doau BufWritePost {url}")
        self.event_sync()

    @pynvim.autocmd("BufEnter", "*", eval='expand("<abuf>")', sync=True)
    @require_state(State.InitialSync, State.Running)
    def _on_buf_enter(self, bufnrstr: str) -> None:
        bufnr = self._vim.buffers[int(bufnrstr)]
        self._set_note_type(bufnrstr)
        self._notelist.update_highlight_and_preview(bufnr)

    @pynvim.function("_gkeep_buf_write_pre", sync=True)
    @unwrap_args
    @require_state(State.Running)
    def _on_buf_write_pre(self, bufnr_str: str) -> None:
        bufnr = self._vim.buffers[int(bufnr_str)]
        url = parser.url_from_file(self._config, bufnr.name, bufnr)
        if url is None or url.id is not None:
            return
        bufname = bufnr.name
        new_bufname = fssync.create_note_from_file(
            self._api, self._config, bufname, url, bufnr
        )
        if new_bufname != bufname:
            self.dispatch("rename_files", {bufname: new_bufname})
        self._notelist.rerun_query()

    @pynvim.function("_gkeep_sync_file", sync=True)
    @unwrap_args
    @require_state(State.Running)
    def _maybe_sync(self, bufnr_str: str) -> None:
        if not self._config.sync_dir:
            return
        bufnr = self._vim.buffers[int(bufnr_str)]
        bufname = bufnr.name
        if NoteUrl.is_ephemeral(bufname):
            return
        url = parser.url_from_file(self._config, bufnr.name, bufnr)
        if url is None:
            return
        note = self._api.get(url.id)
        if note is None:
            new_bufname = fssync.create_note_from_file(
                self._api, self._config, bufname, url, bufnr
            )
            if new_bufname is not None:
                self.dispatch("rename_files", {bufname: new_bufname})
        else:
            parser.parse(self._api, self._config, bufnr, note)
        self._noteview.render(bufnr, NoteUrl.from_note(note))
        self._notelist.rerender_note(note.id)
        self.event_sync()

    @pynvim.function("_gkeep_set_note_type", sync=True)
    @unwrap_args
    @require_state(State.InitialSync, State.Running)
    def _set_note_type(self, bufnr_str: t.Union[int, str]) -> None:
        if not self._config.sync_dir:
            return
        bufnr = self._vim.buffers[int(bufnr_str)]
        url = parser.url_from_file(self._config, bufnr.name, bufnr)
        if url is not None:
            note = self._api.get(url.id)
            if note is not None:
                nt = get_type(note)
                bufnr.vars["note_type"] = nt.value

    @pynvim.function("_gkeep_search", sync=True)
    @unwrap_args
    def search(self, querystr: str, callback: str) -> None:
        query = Query(querystr)

        def respond() -> None:
            results = self._api.get_search(query)
            notes = []
            for note in results:
                url = NoteUrl.from_note(note)
                if isinstance(note, List):
                    icon = self._config.get_icon("list")
                else:
                    icon = self._config.get_icon("note")
                if note.trashed:
                    icon2 = self._config.get_icon("trashed")
                elif note.archived:
                    icon2 = self._config.get_icon("archived")
                else:
                    icon2 = ""
                notes.append(
                    {
                        "id": note.id,
                        "icon": icon,
                        "icon2": icon2,
                        "color": f"GKeep{note.color.value}",
                        "title": note.title,
                        "filename": url.bufname(self._api, self._config, note),
                    }
                )
            self._vim.async_call(
                self._vim.exec_lua, callback, querystr, query.match_str, notes
            )

        if self._config.state != State.Running:
            respond()
            return
        self._api.run_search(query, respond)

    @pynvim.function("_gkeep_render_note", sync=True)
    @unwrap_args
    @require_state(State.Running, log=True)
    def render_note(self, bufstr: str, id: str) -> None:
        buffer = self._vim.buffers[int(bufstr)]
        note = self._api.get(id)
        if note is None:
            return
        url = NoteUrl.from_note(note)
        self._noteview.render(buffer, url)

    @require_state(State.Running)
    def event_rename_files(self, files: t.Dict[str, str]) -> None:
        open_buffers = []
        # Separate the files that nvim has open
        for buffer in self._vim.buffers:
            if not buffer.valid:
                continue
            bufname = buffer.name
            dst = files.pop(bufname, None)
            if dst is None:
                continue
            if NoteUrl.is_ephemeral(dst):
                buffer.name = dst
            else:
                open_buffers.append((buffer, bufname, dst))

        # Rename all files that are not open in vim
        for src, dst in files.items():
            if os.path.exists(src):
                if NoteUrl.is_ephemeral(dst):
                    logger.info("Deleting %s", src)
                    os.unlink(src)
                else:
                    logger.info("Renaming %s -> %s", src, dst)
                    try:
                        os.rename(src, dst)
                    except Exception:
                        logger.exception("Error renaming file %s -> %s", src, dst)

        # I tried doing this logic in python, but any combination of bufadd(),
        # bufload(), :edit, or anything else caused a segfault as soon as I tried to
        # delete the old buffer. I found that using :saveas works, but I can only do
        # that in lua because it has nvim_buf_call.
        self._vim.exec_lua("require('gkeep').rename_buffers(...)", open_buffers)
        for _, src, __ in open_buffers:
            if os.path.exists(src):
                logger.info("Deleting %s", src)
                os.unlink(src)

    @require_state(State.Running, log=True)
    def cmd_logout(self) -> None:
        logger.debug("Logging out")
        email = self._config.email
        if email is not None:
            keyring.delete_password("google-keep-token", email)
            self._config.email = None
        self._config.state = State.Uninitialized
        self._config.delete_state()
        self._api.logout()
        self._menu.refresh(True)
        self._notelist.rerun_query()

    @require_state(State.Uninitialized, State.Running)
    def cmd_login(self, email: str = None, password: str = None) -> None:
        if email is None:
            last_email = self._config.email
            if last_email is not None:
                email = last_email
        show = partial(
            self._modal.prompt.show, relative="editor", width=60, align=Align.SW
        )
        if email is None:
            return show(
                self.cmd_login, prompt=self._config.get_icon("email") + "Email: "
            )

        token = keyring.get_password("google-keep-token", email)
        if token:
            self._resume(email, token, self._config.load_state())
        elif password is None:
            return show(
                partial(self.cmd_login, email),
                prompt=self._config.get_icon("lock"),
                secret=True,
            )
        else:
            self._api.login(email, password, sync=False)
            token = self._api.getMasterToken()
            assert token is not None
            keyring.set_password("google-keep-token", email, token)
            self._resume(email, token, None)
        self._config.email = email
        self._vim.out_write(f"Gkeep logged in {email}\n")

    @require_state(inv=[State.ShuttingDown])
    def cmd_enter(
        self, target: str = "menu", position: Position = Position.LEFT
    ) -> None:
        position = Position(position)
        if target == "menu":
            self._menu.open(True, position)
        elif target == "list":
            self._menu.open(False, position)
            winid = self._notelist.get_win()
            if winid is not None:
                self._vim.current.window = winid
        else:
            self._vim.err_write(f"Unknown target '{target}'\n")

    @require_state(State.Running)
    def cmd_goto(self) -> None:
        lnum, col = self._vim.current.window.cursor
        line = self._vim.current.buffer[lnum - 1]
        note_id = self._find_markdown_link(line, col)
        if note_id is None:
            note_id = self._find_url_link(line, col)
        if note_id is not None:
            note = self._api.get(note_id)
            if note is not None:
                url = NoteUrl.from_note(note)
                bufname = url.bufname(self._api, self._config, note)
                self._vim.command(f"edit {bufname}")

    def _get_current_note(self) -> t.Optional[TopLevelNode]:
        buffer = self._vim.current.buffer
        if buffer == self._notelist.bufnr:
            return self._notelist.get_note_under_cursor()
        else:
            url = parser.url_from_file(self._config, buffer.name, buffer)
            if url is not None and url.id is not None:
                return self._api.get(url.id)
        return None

    @require_state(State.InitialSync, State.Running)
    def cmd_browse(self) -> None:
        note = self._get_current_note()
        if note is None:
            self._vim.err_write("Google Keep note not found\n")
            return
        if note is None:
            self._vim.err_write("Google Keep note not found\n")
            return
        link = util.get_link(note)
        cmd = None
        if self._vim.funcs.executable("open"):
            cmd = "open"
        elif self._vim.funcs.executable("xdg-open"):
            cmd = "xdg-open"
        else:
            cmd = os.environ.get("BROWSER")
        if cmd is None:
            self._vim.err_write(
                "Could not find web browser. Set the BROWSER environment variable and restart\n"
            )
        else:
            subprocess.call([cmd, link])

    @require_state(State.InitialSync, State.Running)
    def cmd_yank(self) -> None:
        note = self._get_current_note()
        if note is None:
            self._vim.err_write("Google Keep note not found\n")
            return
        line = f"[{note.title}]({note.id})"
        self._vim.funcs.setreg("x", line)
        self._vim.funcs.setreg("", line)
        if "unnamedplus" in self._vim.options["clipboard"]:
            self._vim.funcs.setreg("+", line)
        elif "unnamed" in self._vim.options["clipboard"]:
            self._vim.funcs.setreg("*", line)

    def _find_markdown_link(self, line: str, col: int) -> t.Optional[str]:
        try:
            link_start = line.rindex("[", 0, col)
        except ValueError:
            return None
        try:
            link_end = line.index(")", col)
        except ValueError:
            return None
        id_re = re.compile(r"\[[^\]]*\]\(([^\)]*)\)")
        match = id_re.match(line[link_start : link_end + 1])
        if match:
            return match[1]
        else:
            return None

    def _find_url_link(self, line: str, col: int) -> t.Optional[str]:
        try:
            link_start = line.rindex("gkeep://", 0, col + 8)
        except ValueError:
            return None
        url = NoteUrl.from_ephemeral_bufname(line[link_start:])
        return url.id

    @require_state(inv=[State.ShuttingDown])
    def cmd_open(self, position: Position = Position.LEFT) -> None:
        position = Position(position)
        self.preload()
        self._menu.open(False, position)

    @require_state(inv=[State.ShuttingDown])
    def cmd_toggle(self, position: Position = Position.LEFT) -> None:
        position = Position(position)
        self.preload()
        self._menu.toggle(False, position)

    @require_state(inv=[State.ShuttingDown])
    def cmd_close(self) -> None:
        self._menu.close()

    @require_state(State.Running, log=True)
    def cmd_refresh(self) -> None:
        if self._api.is_dirty:
            return self._modal.confirm.show(
                "This will discard your local changes. Are you sure?",
                lambda result: self.event_sync(True)
                if result == ConfirmResult.YES
                else None,
                initial_value=ConfirmResult.NO,
            )
        self.event_sync(True)

    def cmd_check(self) -> None:
        bufnr = self._vim.current.buffer
        url = parser.url_from_file(self._config, bufnr.name, bufnr)
        if url is None:
            return self._vim.err_write("Not inside a Google Keep note\n")
        note = self._api.get(url.id)
        if note is None:
            return

        self._vim.current.line = keep.toggle_list_item(self._vim.current.line)
