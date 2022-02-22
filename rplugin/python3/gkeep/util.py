import enum
import logging
import os
import re
import subprocess
import sys
import typing as t
import unicodedata
from typing import TYPE_CHECKING

import pynvim
from gkeep.config import KEEP_FT, Config, State
from gkeepapi.node import List, NodeType, Note, TopLevelNode
from pynvim.api import Buffer

if sys.version_info < (3, 8):
    from typing_extensions import Literal
else:
    from typing import Literal

if TYPE_CHECKING:
    from gkeep.api import KeepApi

logger = logging.getLogger(__name__)

NoteType = t.Union[List, Note]


class NoteUrl:
    def __init__(
        self,
        id: t.Optional[str],
        title: t.Optional[str] = None,
    ):
        self.id = id or None
        self.title = title or None

    @classmethod
    def from_note(cls, note: TopLevelNode) -> "NoteUrl":
        return cls(note.id, note.title)

    @classmethod
    def from_ephemeral_bufname(cls, bufname: str) -> "NoteUrl":
        assert cls.is_ephemeral(bufname)
        id, name = bufname.split("/", 3)[2:]
        name = os.path.splitext(name)[0]
        return cls(id, name)

    @staticmethod
    def is_ephemeral(bufname: str) -> bool:
        return bufname.startswith("gkeep://")

    def filepath(
        self, api: "KeepApi", config: Config, note: TopLevelNode
    ) -> t.Optional[str]:
        if note.trashed or config.sync_dir is None:
            return None
        elif note.archived and not config.sync_archived_notes:
            return None
        filename = self._get_filename(api, config, note)
        if note.archived:
            assert config.archive_sync_dir is not None
            return os.path.join(config.archive_sync_dir, filename)
        else:
            return os.path.join(config.sync_dir, filename)

    def bufname(self, api: "KeepApi", config: Config, note: TopLevelNode) -> str:
        filepath = self.filepath(api, config, note)
        if filepath is not None:
            return filepath
        return self.ephemeral_bufname(config, note)

    def _get_filename(self, api: "KeepApi", config: Config, note: TopLevelNode) -> str:
        from gkeep import parser

        ext = parser.get_ext(config, note)
        if api.has_unique_title(note):
            return escape(f"{self.title}.{ext}")
        else:
            return escape(f"{self.title}:{self.id}.{ext}")

    def ephemeral_bufname(
        self, config: Config, note: t.Union[TopLevelNode, str]
    ) -> str:
        from gkeep import parser

        title = normalize_title(self.title or "")

        return f"gkeep://{self.id or ''}/{title or ''}.{parser.get_ext(config, note)}"

    def __str__(self) -> str:
        return f"gkeep://{self.id or ''}/{self.title or ''}"

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: t.Any) -> bool:
        if isinstance(other, NoteUrl):
            return self.id == other.id
        return False


def dispatch(vim: pynvim.Nvim, config: Config, func: str, *args: t.Any) -> None:
    if config.state != State.ShuttingDown:
        vim.async_call(vim.exec_lua, "require'gkeep'.dispatch(...)", func, *args)


def echoerr(vim: pynvim.Nvim, message: str) -> None:
    vim.api.echo(
        [(message, "Error")],
        True,
        {},
    )


def checkbox(checked: t.Union[bool, Literal["partial"]]) -> str:
    if checked == "partial":
        return "[-] "
    elif checked:
        return "[x] "
    else:
        return "[ ] "


class NoteEnum(enum.Enum):
    NOTE = "note"
    LIST = "list"


class NoteFormat(enum.Enum):
    NOTE = "note"
    LIST = "list"
    NEORG = "neorg"


def get_type(note: TopLevelNode) -> NoteEnum:

    if note.type == NodeType.Note:
        return NoteEnum.NOTE
    elif note.type == NodeType.List:
        return NoteEnum.LIST
    else:
        raise TypeError(f"Unknown note type: {note.type}")


def get_link(note: TopLevelNode) -> str:
    return f"https://keep.google.com/#NOTE/{note.server_id}"


def escape(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title)
    # Replace all whitespace with a space character
    normalized = re.sub(r"\s", " ", normalized)
    return re.sub(r"[^\w\s\.-]", "", normalized)


def normalize_title(title: str) -> str:
    return re.sub(r"\s", " ", title)


def get_ext(filename: str) -> str:
    ext = os.path.splitext(filename)[1]
    # Handle the case of an empty title
    if not ext:
        basename = os.path.basename(filename)
        if basename.startswith("."):
            ext = basename
    return ext.lower()


def set_note_opts_and_vars(note: NoteType, bufnr: Buffer) -> None:
    nt = get_type(note)
    bufnr.vars["note_type"] = nt.value
    if bufnr.options["filetype"] == KEEP_FT:
        shiftwidth = 2 if nt == NoteEnum.NOTE else 4
        bufnr.options["shiftwidth"] = shiftwidth


def open_url(vim: pynvim.Nvim, url: str) -> None:
    cmd = None
    if vim.funcs.executable("open"):
        cmd = "open"
    elif vim.funcs.executable("xdg-open"):
        cmd = "xdg-open"
    else:
        cmd = os.getenv("BROWSER")
    if cmd is None:
        echoerr(
            vim,
            "Could not find web browser. Set the BROWSER environment variable and restart",
        )
    else:
        subprocess.call([cmd, url])
