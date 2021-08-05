import logging
import os
import typing as t

from gkeep import util
from gkeep.api import KeepApi
from gkeep.config import KEEP_FT, Config
from gkeep.parser import keep, neorg
from gkeep.parser.common import TFile, read_lines
from gkeep.util import NoteUrl
from gkeepapi.node import List, Note, TopLevelNode
from pynvim.api import Buffer

logger = logging.getLogger(__name__)

__all__ = [
    "ALLOWED_EXT",
    "TFile",
    "get_ext",
    "get_filetype",
    "parse",
    "read_lines",
    "serialize",
]

ALLOWED_EXT = {".keep", ".norg"}


def get_filetype(config: Config, note_or_str: t.Union[TopLevelNode, str]) -> str:
    if isinstance(note_or_str, List) or not config.support_neorg:
        return KEEP_FT
    if isinstance(note_or_str, str):
        text = note_or_str
    else:
        text = note_or_str.text
    if neorg.has_document_meta(text):
        return "norg"
    else:
        return KEEP_FT


def get_ext(config: Config, note_or_str: t.Union[TopLevelNode, str]) -> str:
    return ext_from_filetype(get_filetype(config, note_or_str))


def ext_from_filetype(filetype: str) -> str:
    if filetype == KEEP_FT:
        return "keep"
    else:
        return filetype


def serialize(
    config: Config, note: TopLevelNode, filetype: str = None
) -> t.Iterator[str]:
    if filetype is None:
        filetype = get_filetype(config, note)
    if filetype == KEEP_FT:
        yield from keep.serialize(note)
        return
    assert isinstance(note, Note)

    if filetype == "norg":
        yield from neorg.serialize(note)
    else:
        logger.warning("Unrecognized filetype %s", filetype)
        yield from note.text.split("\n")


def convert(note: Note, from_ft: t.Optional[str], to_ft: str) -> None:
    # Right now this logic is dead simple because we only support KEEP_FT and neorg
    if from_ft == to_ft:
        return
    elif to_ft == "norg":
        neorg.convert_to_neorg(note)
    elif from_ft == "norg":
        neorg.convert_from_neorg(note)


def parse(api: KeepApi, config: Config, file: TFile, note: TopLevelNode) -> None:
    lines = read_lines(file)
    ft = get_filetype(config, note)
    if ft == KEEP_FT:
        keep.parse(api, lines, note)
        return
    assert isinstance(note, Note)

    if ft == "norg":
        neorg.parse(api, lines, note)
    else:
        logger.warning("Cannot parse filetype %s", ft)
        keep.parse_note_body(lines, note, 0)


def url_from_file(
    config: Config, filename: str, file: TFile = None
) -> t.Optional[NoteUrl]:
    if file is None:
        file = filename
    if NoteUrl.is_ephemeral(filename):
        return NoteUrl.from_ephemeral_bufname(filename)
    elif config.sync_dir is None:
        return None
    if not os.path.isabs(filename):
        filename = os.path.abspath(filename)
    if not filename.startswith(config.sync_dir):
        return None
    basename, ext = os.path.splitext(os.path.basename(filename))
    ext = ext.lower()
    if ext not in ALLOWED_EXT:
        return None

    if ext == ".norg":
        header = neorg.get_metadata(file)
    else:
        header = keep.get_metadata(file)
    return NoteUrl(header.id, header.title or basename)


def write_file_metadata(filename: str, id: str, title: str, file: TFile = None) -> None:
    ext = util.get_ext(filename)
    if ext == ".norg":
        neorg.write_file_meta(filename, id, title, file)
    else:
        keep.write_file_meta(filename, id, title, file)


def write_buffer_metadata(buffer: Buffer, id: str, title: str) -> None:
    ext = util.get_ext(buffer.name)
    if ext == ".norg":
        neorg.write_buffer_meta(buffer, id, title)
    else:
        keep.write_buffer_meta(buffer, id, title)
