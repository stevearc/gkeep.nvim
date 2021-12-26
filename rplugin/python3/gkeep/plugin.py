import logging
import logging.handlers
import os
import re
import sys
import time
import typing as t
from functools import partial, wraps
from uuid import getnode

import gkeep.modal
import gpsoauth
import keyring
import pynvim
from gkeep import fssync, parser, util
from gkeep.api import KeepApi
from gkeep.config import Config, State
from gkeep.modal import Align, ConfirmResult
from gkeep.parser import ALLOWED_EXT, keep
from gkeep.query import Query
from gkeep.status import get_status
from gkeep.thread_util import background
from gkeep.util import NoteFormat, NoteUrl
from gkeep.views import menu, notelist, notepopup, noteview
from gkeep.views.menu import Position
from gkeepapi.exception import LoginException
from gkeepapi.node import List, TopLevelNode

if sys.version_info < (3, 8):
    from typing_extensions import TypedDict
else:
    from typing import TypedDict

logger = logging.getLogger(__name__)
LINK_RE = re.compile(r"\[[^\]]*\]\(([^\)]*)\)")


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
                    util.echoerr(
                        self._vim, f"Cannot perform action while Gkeep is {state.name}"
                    )
            else:
                f(self, *args, **kwargs)

        return w  # type: ignore[return-value]

    return d


@pynvim.plugin
class GkeepPlugin:
    def __init__(self, vim: pynvim.Nvim) -> None:
        self._vim = vim
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
        self._noteview = noteview.NoteView(vim, self._config, self._api)
        self._notelist = notelist.NoteList(
            vim, self._config, self._api, self._modal, self._noteview
        )
        self._notepopup = notepopup.NotePopup(
            vim, self._config, self._api, self._modal, self._noteview
        )
        self._menu = menu.Menu(
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
        self._vim.exec_lua("(function() end)()")
        if self._config.sync_dir:
            self._sync: fssync.ISync = fssync.FileSync(self._api, self._config)
        else:
            self._sync = fssync.NoopSync(self._api)
        self._start_callbacks: t.List[t.Callable[[], None]] = []

    @pynvim.shutdown_hook
    def on_shutdown(self) -> None:
        self._config.state = State.ShuttingDown

    @background(max_waiting=0)
    def sync_bg_thread(self) -> None:
        while self._config.state != State.ShuttingDown:
            time.sleep(10)
            self.dispatch("sync")

    @pynvim.function("_gkeep_health", sync=True)
    @unwrap_args
    def health_report(self) -> t.Dict[str, t.Any]:
        email = self._api.get_email()
        # censor email because we're going to ask people to paste :checkhealth output in
        # github issues
        if email is not None:
            pieces = email.split("@")
            pieces[0] = pieces[0][:3] + "*" * (len(pieces[0]) - 3)
            email = "@".join(pieces)
        try:
            keyring.get_password("google-keep-token", email or "")
        except Exception as e:
            keyring_err = str(e)
        else:
            keyring_err = ""
        return {
            "logged_in": self._api.is_logged_in,
            "email": email,
            "support_neorg": self._config.support_neorg,
            "sync_dir": self._config.sync_dir,
            "keyring_err": keyring_err,
        }

    @pynvim.function("GkeepStatus", sync=True)
    @unwrap_args
    def get_status_str(self) -> str:
        st = get_status()
        return "" if st is None else st

    @pynvim.function("_gkeep_complete_position", sync=True)
    @unwrap_args
    def _gkeep_complete_position(
        self, arg_lead: str, _line: str, _cursor_pos: int
    ) -> t.List[str]:
        return _complete_arg_list(arg_lead, [p.value for p in Position])

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

    @pynvim.function("_gkeep_preload_if_any_open")
    @unwrap_args
    def preload_if_any_open(self) -> None:
        for buffer in self._vim.buffers:
            if NoteUrl.is_ephemeral(buffer.name):
                self.preload()
                return

    @pynvim.function("_gkeep_dispatch", sync=True)
    @unwrap_args
    def dispatch_event(self, event: str, *args: t.Any) -> None:
        meth = f"event_{event}"
        if hasattr(self, meth):
            getattr(self, meth)(*args)
        else:
            util.echoerr(self._vim, f"Unknown Gkeep event '{event}'")

    @background
    @require_state(State.Uninitialized)
    def _resume(self, email: str, token: str, state: t.Optional[t.Any]) -> None:
        self._api.resume(email, token, state=None, sync=False)
        if not self._api.is_logged_in:
            return
        logger.info("Resuming gkeep session for %s", email)
        self._sync.start(state)
        self._config.state = State.InitialSync
        self._api.sync(
            self._finish_initial_sync,
            partial(self.dispatch, "handle_sync_error"),
        )
        self.dispatch("refresh", True)

    def _finish_initial_sync(
        self,
        resync: bool,
        keep_version: str,
        user_info: t.Any,
        nodes: t.Sequence[t.Any],
    ) -> None:
        updated_notes = self._api.apply_updates(resync, keep_version, user_info, nodes)
        self._config.save_state(self._api.dump())
        logger.debug("Startup sync complete. Updated %d notes", len(updated_notes))
        renames = self._sync.finish_startup(updated_notes)
        self._config.state = State.Running
        self.dispatch("rename_files", renames)
        self.dispatch("refresh", True)
        self.dispatch("sync")
        self.dispatch("on_start")
        self.sync_bg_thread()

    @require_state(State.Running)
    def event_sync(self, resync: bool = False, force: bool = False) -> None:
        # We may have opened vim on a note file. If so, we will need to set the
        # note_type once the api has loaded
        self._set_note_type(self._vim.current.buffer.number)

        if not force and not resync and not self._api.is_dirty:
            return
        logger.debug("Syncing gkeep %s", "(force refresh)" if resync else "")
        self._api.sync(
            partial(self.dispatch, "finish_sync"),
            partial(self.dispatch, "handle_sync_error"),
            resync,
        )

    def event_handle_sync_error(self, error: str) -> None:
        logger.error("Got error during sync: %s\n  deactivating gkeep!", error)
        util.echoerr(
            self._vim, "Google Keep sync error. Use :GkeepLogin to re-initialize"
        )
        self._reset_all_state()

    @require_state(State.Running)
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
        notes = []
        for id in updated_notes:
            note = self._api.get(id)
            if note is not None:
                notes.append(fssync.NoteFile.from_note(self._api, self._config, note))

        # Only rerender the ephemeral buffers. Note files are updated directly.
        for bufnr in self._vim.buffers:
            bufname = bufnr.name
            if NoteUrl.is_ephemeral(bufname):
                url = NoteUrl.from_ephemeral_bufname(bufname)
                if url.id in updated_notes and not bufnr.options["modified"]:
                    self._noteview.render(bufnr, url)
        self._write_files(notes)

    @background
    def _write_files(self, updated_notes: t.Sequence[fssync.NoteFile]) -> None:
        renames = self._sync.write_files(updated_notes)
        if renames:
            self.dispatch("rename_files", renames)

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

    @require_state(State.Running)
    def event_on_start(self) -> None:
        # Rerender any ephemeral buffers that are open
        for bufnr in self._vim.buffers:
            bufname = bufnr.name
            if NoteUrl.is_ephemeral(bufname) and not bufnr.options["modified"]:
                url = NoteUrl.from_ephemeral_bufname(bufname)
                self._noteview.render(bufnr, url)
        for cb in self._start_callbacks:
            cb()

    @pynvim.function("_gkeep_list_action", sync=True)
    @unwrap_args
    @require_state(State.InitialSync, State.Running)
    def list_action(self, action: str, *args: t.Any) -> None:
        # Only allow read actions until state is Running
        read_actions = ["preview", "select", "cursor_moved", "update_preview"]
        if self._config.state == State.InitialSync and action not in read_actions:
            return
        self._notelist.action(action, *args)

    @pynvim.function("_gkeep_popup_action", sync=True)
    @unwrap_args
    @require_state(State.Running)
    def popup_action(self, action: str, *args: t.Any) -> None:
        self._notepopup.action(action, *args)

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
    @require_state(State.InitialSync, State.Running, log=True)
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
        ext = util.get_ext(bufnr.name)[1:]
        self._vim.exec_lua("require('gkeep').on_ephemeral_buf_read(...)", ext)

    @pynvim.autocmd("BufWriteCmd", "gkeep://*", eval='expand("<abuf>")', sync=True)
    @require_state(State.InitialSync, State.Running, log=True)
    def _save_note(self, bufnrstr: str) -> None:
        bufnr = self._vim.buffers[int(bufnrstr)]
        url = parser.url_from_file(self._config, bufnr.name, bufnr)
        if not url:
            util.echoerr(self._vim, f"Buffer {bufnrstr} has malformed Gkeep bufname")
            return
        if self._config.state == State.InitialSync:
            util.echoerr(self._vim, "Cannot save: Gkeep is still starting up")
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
        """Called when saving a buffer

        Detects if note buffer and, if so, parses & syncs the note
        """
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
                util.set_note_opts_and_vars(note, bufnr)

    def _note_to_result(self, note: TopLevelNode) -> t.Dict:
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
        return {
            "id": note.id,
            "icon": icon,
            "icon2": icon2,
            "color": f"GKeep{note.color.value}",
            "title": note.title,
            "filename": url.bufname(self._api, self._config, note),
        }

    @pynvim.function("_gkeep_search", sync=True)
    @unwrap_args
    def search(self, querystr: str, callback: str) -> None:
        query = Query(querystr)

        def respond() -> None:
            results = self._api.get_search(query)
            notes = [self._note_to_result(n) for n in results]
            self._vim.async_call(
                self._vim.exec_lua, callback, querystr, query.match_str, notes
            )

        if self._config.state not in (State.InitialSync, State.Running):
            respond()
            return
        self._api.run_search(query, respond)

    @pynvim.function("_gkeep_all_notes", sync=True)
    @unwrap_args
    def get_titles(self) -> t.Optional[t.List[t.Dict]]:
        if self._config.state not in (State.InitialSync, State.Running):
            return None
        return [self._note_to_result(n) for n in self._api.all()]

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
        # that in lua because it requires nvim_buf_call.
        self._vim.exec_lua("require('gkeep').rename_buffers(...)", open_buffers)
        for _, src, __ in open_buffers:
            if os.path.exists(src):
                logger.info("Deleting %s", src)
                os.unlink(src)

    def _reset_all_state(self) -> None:
        self._config.state = State.Uninitialized
        self._config.delete_state()
        self._api.logout()
        self._menu.refresh(True)

    @pynvim.command("GkeepLogout", sync=True)
    @require_state(State.Running, log=True)
    @unwrap_args
    def cmd_logout(self) -> None:
        logger.debug("Logging out")
        email = self._config.email
        if email is not None:
            keyring.delete_password("google-keep-token", email)
            self._config.email = None
        self._reset_all_state()

    @pynvim.command("GkeepLogin", nargs="*", sync=True)
    @require_state(State.Uninitialized, State.Running)
    @unwrap_args
    def cmd_login(self, email: str = None, password: str = None) -> None:
        prompt = partial(
            self._modal.prompt.show, relative="editor", width=60, align=Align.SW
        )
        if email is None:
            return prompt(
                self.cmd_login, prompt=self._config.get_icon("email") + "Email: "
            )
        self._config.email = email
        if self._config.state == State.Running:
            self._reset_all_state()

        token = keyring.get_password("google-keep-token", email)
        if token:
            self._resume(email, token, self._config.load_state())
        elif password is None:
            return prompt(
                partial(self.cmd_login, email),
                prompt=self._config.get_icon("lock"),
                secret=True,
            )
        else:
            try:
                self._api.login(email, password, sync=False)
            except LoginException as e:
                res = gpsoauth.perform_master_login(email, password, str(getnode()))
                url = res.get("Url")
                if url:
                    util.open_url(self._vim, url)
                    self._vim.out_write(
                        "Complete login flow in browser, then re-attempt GkeepLogin.\n"
                    )
                    return
                else:
                    util.echoerr(
                        self._vim,
                        f"Unknown error logging in; please report an issue on github. API error: {e}",
                    )
                    return

            token = self._api.getMasterToken()
            assert token is not None
            keyring.set_password("google-keep-token", email, token)
            self._resume(email, token, None)
        self._vim.out_write(f"Gkeep logged in {email}\n")

    @pynvim.command(
        "GkeepEnter", nargs="*", complete="customlist,_gkeep_complete_enter", sync=True
    )
    @require_state(inv=[State.ShuttingDown])
    @unwrap_args
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
            util.echoerr(self._vim, f"Unknown target '{target}'")

    @pynvim.function("_gkeep_complete_enter", sync=True)
    @unwrap_args
    def _gkeep_complete_enter(
        self, arg_lead: str, line: str, cursor_pos: int
    ) -> t.List[str]:
        return _complete_multi_arg_list(
            arg_lead, line, cursor_pos, ["menu", "list"], [p.value for p in Position]
        )

    @pynvim.command("GkeepGoto", sync=True)
    @require_state(State.Running)
    @unwrap_args
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
        elif buffer == self._notepopup.bufnr:
            return self._notepopup.note
        else:
            url = parser.url_from_file(self._config, buffer.name, buffer)
            if url is not None and url.id is not None:
                return self._api.get(url.id)
        return None

    @pynvim.command("GkeepPopup", sync=True)
    @require_state(State.Running)
    @unwrap_args
    def cmd_popup(self) -> None:
        self._notepopup.toggle()

    @pynvim.command("GkeepBrowse", sync=True)
    @require_state(State.InitialSync, State.Running)
    @unwrap_args
    def cmd_browse(self) -> None:
        note = self._get_current_note()
        if note is None:
            util.echoerr(self._vim, "Google Keep note not found")
            return
        if note is None:
            util.echoerr(self._vim, "Google Keep note not found")
            return
        link = util.get_link(note)
        util.open_url(self._vim, link)

    @pynvim.command(
        "GkeepNew", nargs="*", complete="customlist,_gkeep_complete_new", sync=True
    )
    @require_state(inv=[State.ShuttingDown])
    @unwrap_args
    def cmd_new(self, *type_and_name_pieces: str) -> None:
        if type_and_name_pieces:
            note_type = NoteFormat(type_and_name_pieces[0])
            note_name = None
            if len(type_and_name_pieces) > 1:
                note_name = " ".join(type_and_name_pieces[1:])
            new_fn: t.Callable[[], None] = partial(self._new_note, note_type, note_name)
        else:
            new_fn = self._new_note
        if self._config.state == State.Uninitialized:
            if not self._config.email:
                util.echoerr(self._vim, "Log in first with :GkeepLogin")
            else:
                self.preload()
                self._start_callbacks.append(new_fn)
        elif self._config.state == State.Running:
            new_fn()
        else:
            self._start_callbacks.append(new_fn)

    @pynvim.function("_gkeep_complete_new", sync=True)
    @unwrap_args
    def _gkeep_complete_new(
        self, arg_lead: str, line: str, cursor_pos: int
    ) -> t.List[str]:
        formats = [NoteFormat.NOTE, NoteFormat.LIST]
        if self._config.support_neorg:
            formats.append(NoteFormat.NEORG)
        return _complete_multi_arg_list(
            arg_lead, line, cursor_pos, [p.value for p in formats]
        )

    @require_state(State.Running, log=True)
    def _new_note(self, type: NoteFormat = None, title: str = None) -> None:
        self._notelist.new_note(type, title)

    @pynvim.command("GkeepYank", sync=True)
    @require_state(State.InitialSync, State.Running)
    @unwrap_args
    def cmd_yank(self) -> None:
        note = self._get_current_note()
        if note is None:
            util.echoerr(self._vim, "Google Keep note not found")
            return
        line = f"[{note.title}]({note.id})"
        self._vim.funcs.setreg("x", line)
        self._vim.funcs.setreg("", line)
        if "unnamedplus" in self._vim.options["clipboard"]:
            self._vim.funcs.setreg("+", line)
        elif "unnamed" in self._vim.options["clipboard"]:
            self._vim.funcs.setreg("*", line)

    @pynvim.command("GkeepUpdateLinks", sync=True)
    @require_state(State.Running)
    def cmd_update_links(self) -> None:
        buffer = self._vim.current.buffer
        lines = buffer[:]

        def update_link(match: re.Match) -> str:
            note = self._api.get(match[1])
            if note is not None:
                return f"[{note.title}]({note.id})"
            return match[0]

        for i, line in enumerate(lines):
            new_line = LINK_RE.sub(update_link, line)
            if line != new_line:
                buffer[i] = new_line

    def _find_markdown_link(self, line: str, col: int) -> t.Optional[str]:
        try:
            link_start = line.rindex("[", 0, col)
        except ValueError:
            return None
        try:
            link_end = line.index(")", col)
        except ValueError:
            return None
        match = LINK_RE.match(line[link_start : link_end + 1])
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

    @pynvim.command(
        "GkeepOpen",
        nargs="?",
        complete="customlist,_gkeep_complete_position",
        sync=True,
    )
    @require_state(inv=[State.ShuttingDown])
    @unwrap_args
    def cmd_open(self, position: Position = Position.LEFT) -> None:
        position = Position(position)
        self.preload()
        self._menu.open(False, position)

    @pynvim.command(
        "GkeepToggle",
        nargs="?",
        complete="customlist,_gkeep_complete_position",
        sync=True,
    )
    @require_state(inv=[State.ShuttingDown])
    @unwrap_args
    def cmd_toggle(self, position: Position = Position.LEFT) -> None:
        position = Position(position)
        self.preload()
        self._menu.toggle(False, position)

    @pynvim.command("GkeepClose", sync=True)
    @require_state(inv=[State.ShuttingDown])
    @unwrap_args
    def cmd_close(self) -> None:
        self._menu.close()

    @pynvim.command("GkeepSync", sync=True)
    @require_state(State.Running, log=True)
    @unwrap_args
    def cmd_sync(self) -> None:
        self.event_sync(resync=False, force=True)

    @pynvim.command("GkeepRefresh", sync=True)
    @require_state(State.Running, log=True)
    @unwrap_args
    def cmd_refresh(self) -> None:
        if self._api.is_dirty:
            return self._modal.confirm.show(
                "This will discard your local changes. Are you sure?",
                lambda result: self.event_sync(True)
                if result == ConfirmResult.YES
                else None,
                initial_value=ConfirmResult.NO,
            )
        self.event_sync(resync=True)

    @pynvim.command("GkeepCheck", sync=True)
    @unwrap_args
    def cmd_check(self) -> None:
        bufnr = self._vim.current.buffer
        url = parser.url_from_file(self._config, bufnr.name, bufnr)
        if url is None:
            return util.echoerr(self._vim, "Not inside a Google Keep note")
        note = self._api.get(url.id)
        if note is None:
            return

        self._vim.current.line = keep.toggle_list_item(self._vim.current.line)


def _complete_arg_list(arg_lead: str, options: t.Iterable[str]) -> t.List[str]:
    return [opt for opt in options if opt.lower().startswith(arg_lead.lower())]


def _get_arg_index(line: str, cursor_pos: int) -> int:
    """Return which argument the cursor is currently on"""
    arg_idx = -1
    i = 0
    while i < cursor_pos:
        if line[i] == " ":
            arg_idx += 1
            # Multiple spaces are treated as a single delimiter
            while i < cursor_pos and line[i] == " ":
                i += 1
            continue
        i += 1
    return arg_idx


def _complete_multi_arg_list(
    arg_lead: str, line: str, cursor_pos: int, *option_lists: t.Iterable[str]
) -> t.List[str]:
    arg_idx = _get_arg_index(line, cursor_pos)
    if arg_idx == -1 or arg_idx >= len(option_lists):
        return []
    else:
        return _complete_arg_list(arg_lead, option_lists[arg_idx])
