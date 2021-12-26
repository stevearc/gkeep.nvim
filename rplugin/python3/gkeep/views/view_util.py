import os
import typing as t
from functools import partial

from gkeep import fssync, parser, util
from gkeep.api import KeepApi
from gkeep.config import KEEP_FT, Config
from gkeep.modal import Element, GridLayout, Modal, TextAlign
from gkeep.util import NoteEnum, NoteFormat, NoteType, NoteUrl
from gkeepapi.node import List, Note
from pynvim.api import Buffer, Nvim


def get_note_format(config: Config, note: NoteType) -> NoteFormat:
    if util.get_type(note) == NoteEnum.LIST:
        return NoteFormat.LIST
    elif parser.get_filetype(config, note) == "norg":
        return NoteFormat.NEORG
    else:
        return NoteFormat.NOTE


def split_type_and_format(note_type: NoteFormat) -> t.Tuple[NoteEnum, str]:
    if note_type == NoteFormat.NOTE:
        return (NoteEnum.NOTE, KEEP_FT)
    elif note_type == NoteFormat.LIST:
        return (NoteEnum.LIST, KEEP_FT)
    elif note_type == NoteFormat.NEORG:
        return (NoteEnum.NOTE, "norg")
    else:
        raise ValueError(f"Invalid note type {note_type}")


def get_local_changed_file(
    config: Config, api: KeepApi, note: NoteType
) -> t.Optional[str]:
    filepath = NoteUrl.from_note(note).filepath(api, config, note)
    if filepath is not None:
        local = fssync.get_local_file(filepath)
        if os.path.exists(local):
            return local
    return None


def render_note_line(
    vim: Nvim,
    config: Config,
    api: KeepApi,
    row: int,
    note: NoteType,
    highlights: t.List[t.Tuple[str, int, int, int]],
) -> str:
    pieces = []
    col = 0
    if isinstance(note, List):
        icon = "list"
    else:
        icon = "note"
    pieces.append(config.get_icon(icon))
    icon_width = config.get_icon_width(icon)
    highlights.append((f"GKeep{note.color.value}", row, col, col + icon_width))
    col += icon_width

    local_file = get_local_changed_file(config, api, note)
    if local_file is not None:
        pieces.append(config.get_icon("diff"))
        icon_width = config.get_icon_width("diff")
        highlights.append(("Error", row, col, col + icon_width))
        col += icon_width

    if note.archived:
        pieces.append(config.get_icon("archived"))
    if note.trashed:
        pieces.append(config.get_icon("trashed"))
    if note.pinned:
        pieces.append(config.get_icon("pinned"))

    entry = note.title.strip()
    if not entry:
        entry = "<No title>"
    pieces.append(entry)
    text = "".join(pieces)
    length = vim.strwidth(text)
    if length < config.width:
        text += " " * (config.width - length)
    return text


def render_note_list(
    vim: Nvim, config: Config, api: KeepApi, buf: Buffer, notes: t.List[NoteType]
) -> None:
    lines = []
    highlights: t.List[t.Tuple[str, int, int, int]] = []
    for i, note in enumerate(notes):
        line = render_note_line(vim, config, api, i, note, highlights)
        lines.append(line)
    buf.options["modifiable"] = True
    buf[:] = lines
    buf.options["modifiable"] = False
    ns = vim.api.create_namespace("GkeepNoteListHL")
    buf.update_highlights(ns, highlights, clear=True)


class NoteTypeEditor:
    def __init__(
        self,
        vim: Nvim,
        config: Config,
        api: KeepApi,
        modal: Modal,
    ):
        self._vim = vim
        self._config = config
        self._api = api
        self._modal = modal
        self.dispatch = partial(util.dispatch, self._vim, self._config)
        self._callback: t.Optional[t.Callable[[NoteType, NoteType], None]] = None

    def get_note_type_layout(self) -> GridLayout[NoteFormat]:
        elements = [
            Element(NoteFormat.NOTE, self._config.get_icon("note") + "Note"),
            Element(NoteFormat.LIST, self._config.get_icon("list") + "List"),
        ]
        if self._config.support_neorg:
            elements.append(
                Element(NoteFormat.NEORG, self._config.get_icon("note") + "Neorg")
            )
        return GridLayout(
            self._vim,
            [elements],
            align=TextAlign.CENTER,
        )

    def change_type(
        self, note: NoteType, callback: t.Callable[[NoteType, NoteType], None] = None
    ) -> None:
        self._callback = callback
        layout = self.get_note_type_layout()
        initial_value = get_note_format(self._config, note)
        self._modal.confirm.show(
            f"Change type of {note.title}",
            partial(self._change_type, note),
            text_margin=2,
            initial_value=initial_value,
            layout=layout,
        )

    def _change_type(self, note: NoteType, new_type: NoteFormat) -> None:
        note_type, filetype = split_type_and_format(new_type)
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

        new_note.touch()
        self._api.add(new_note)

        new_bufname = url.bufname(self._api, self._config, note)
        if bufname != new_bufname:
            self.dispatch("rename_files", {bufname: new_bufname})
        self.dispatch("sync")
        if self._callback is not None:
            self._callback(note, new_note)
