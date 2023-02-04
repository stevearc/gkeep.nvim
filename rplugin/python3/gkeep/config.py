import enum
import json
import logging
import os
import typing as t
from pathlib import Path

import pynvim
from gkeep.thread_util import background
from gkeep.views.menuitem import MenuItem

logger = logging.getLogger(__name__)

KEEP_FT = "GoogleKeepNote"

__all__ = ["Config", "KEEP_FT"]


class State(enum.IntEnum):
    Uninitialized = 0
    InitialSync = 1
    Running = 2
    ShuttingDown = 3


DEFAULT_ICONS = {
    "nerdfont": {
        "email": " ",
        "home": " ",
        "label": " ",
        "list": "ﱔ ",
        "lock": " ",
        "neorg": " ",
        "note": " ",
        "pinned": "車",
        "search": " ",
        "trashed": " ",
        "archived": " ",
        "diff": "繁",
    },
    "unicode": {
        "email": "📧 ",
        "home": "💡",
        "label": "⇨ ",
        "list": "✔ ",
        "lock": "🔒 ",
        "neorg": "■ ",
        "note": "■ ",
        "pinned": "* ",
        "search": "🔍 ",
        "trashed": "D ",
        "archived": "A ",
        "diff": "⇆ ",
    },
}

DEFAULT_LOG_LEVELS = {
    "gkeep": "WARNING",
    "gkeepapi": "WARNING",
}


class Config:
    def __init__(
        self,
        cache_dir: t.Union[str, Path],
        sync_dir: t.Optional[t.Union[str, Path]] = None,
        support_neorg: bool = False,
        icons: t.Optional[t.Dict[str, str]] = None,
        icon_width: t.Optional[t.Dict[str, int]] = None,
        log_levels: t.Optional[t.Dict[str, int]] = None,
        width: int = 32,
        sync_archived_notes: bool = False,
    ) -> None:
        self._email: t.Optional[str] = None
        self._saved_searches: t.List[MenuItem] = []
        self._cache_file = os.path.join(cache_dir, "gkeep.json")
        self._state_file = os.path.join(cache_dir, "gkeep_state.json")
        self._sync_dir = os.path.abspath(sync_dir) if sync_dir is not None else None
        self._support_neorg = support_neorg
        self._icons = icons or {}
        self._icon_width = icon_width or {}
        self.state = State.Uninitialized
        self.log_levels = log_levels or {}
        self._width = width
        self._sync_archived_notes = sync_archived_notes
        self.load_cache()

    @classmethod
    def from_vim(cls, vim: pynvim.Nvim) -> "Config":
        cache_dir = vim.funcs.stdpath("cache")
        sync_dir = vim.vars.get("gkeep_sync_dir")
        if sync_dir:
            sync_dir = os.path.expanduser(sync_dir)
        sync_archived_notes = bool(vim.vars.get("gkeep_sync_archived", False))
        neorg = t.cast(bool, vim.exec_lua("return package.loaded.neorg ~= nil"))
        nerd_font = bool(vim.vars.get("gkeep_nerd_font", True))
        user_icons = vim.vars.get("gkeep_icons", {})
        icons = {}
        icons.update(
            DEFAULT_ICONS["nerdfont"] if nerd_font else DEFAULT_ICONS["unicode"]
        )
        icons.update(user_icons)
        icon_width = {}
        for k, v in icons.items():
            icon_width[k] = vim.funcs.strlen(v)
        str_log_levels = DEFAULT_LOG_LEVELS.copy()
        str_log_levels.update(vim.vars.get("gkeep_log_levels", {}))
        log_levels = {
            k: logging.getLevelName(v.upper()) for k, v in str_log_levels.items()
        }
        width = vim.vars.get("gkeep_width", 32)
        return cls(
            cache_dir,
            sync_dir,
            neorg,
            icons,
            icon_width,
            log_levels,
            width,
            sync_archived_notes,
        )

    def reload_from_vim(self, vim: pynvim.Nvim) -> None:
        self._support_neorg = t.cast(
            bool, vim.exec_lua("return package.loaded.neorg ~= nil")
        )

    def get_icon(self, icon: str) -> str:
        return self._icons.get(icon, "")

    def get_icon_width(self, icon: str) -> int:
        w = self._icon_width.get(icon)
        if w is not None:
            return w
        return len(self.get_icon(icon))

    def ft_from_ext(self, ext: str) -> str:
        if ext.startswith("."):
            ext = ext[1:]
        if ext == "keep":
            return KEEP_FT
        else:
            return ext

    @property
    def support_neorg(self) -> bool:
        return self._support_neorg

    @property
    def width(self) -> int:
        return self._width

    @property
    def sync_dir(self) -> t.Optional[str]:
        return self._sync_dir

    @property
    def sync_archived_notes(self) -> bool:
        return self._sync_archived_notes

    @property
    def archive_sync_dir(self) -> t.Optional[str]:
        if self.sync_dir and self.sync_archived_notes:
            return os.path.join(self.sync_dir, "archived")
        return None

    @property
    def email(self) -> t.Optional[str]:
        return self._email

    @email.setter
    def email(self, email: t.Optional[str]) -> None:
        self._email = email
        self.save_cache()

    @property
    def saved_searches(self) -> t.List[MenuItem]:
        return self._saved_searches

    def add_saved_search(self, search: MenuItem) -> None:
        self._saved_searches.append(search)
        self.save_cache()

    def remove_saved_search(self, search: MenuItem) -> None:
        self._saved_searches.remove(search)
        self.save_cache()

    def load_cache(self) -> None:
        file = self._cache_file
        if not os.path.isfile(file):
            return
        with open(file, "r") as ifile:
            data = json.load(ifile)
        self._email = data.get("email")
        self._saved_searches.clear()
        for name, query in data.get("saved_searches", []):
            self._saved_searches.append(MenuItem("search", name, query))

    def save_cache(self) -> None:
        with open(self._cache_file, "w") as ofile:
            json.dump(
                {
                    "email": self.email,
                    "saved_searches": [(i.name, i.query) for i in self._saved_searches],
                },
                ofile,
            )

    @background
    def save_state(self, state: t.Any) -> None:
        with open(self._state_file, "w") as ofile:
            json.dump(state, ofile)

    def load_state(self) -> t.Union[t.Any, None]:
        if not os.path.isfile(self._state_file):
            return None
        with open(self._state_file, "r") as ifile:
            try:
                return json.load(ifile)
            except json.JSONDecodeError:
                return None

    def delete_state(self) -> None:
        self._saved_searches.clear()
        if os.path.isfile(self._state_file):
            os.unlink(self._state_file)
