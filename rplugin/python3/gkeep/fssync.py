import hashlib
import logging
import os
import typing as t

import gkeep.parser as parser
from gkeep.api import KeepApi
from gkeep.config import Config
from gkeep.parser.keep import detect_note_type
from gkeep.util import NoteEnum, NoteUrl
from gkeepapi.node import List, Note, TopLevelNode
from pynvim.api import Buffer

logger = logging.getLogger(__name__)


def find_files_with_changes(api: KeepApi, config: Config) -> t.Iterator[str]:
    if config.sync_dir is None:
        return
    logger.debug("Loading gkeep files from %s", config.sync_dir)
    for filename, url in find_files(config):
        note = load_file(api, config, filename, url)
        if note is None or note.dirty:
            yield filename


def find_files(config: Config) -> t.Iterator[t.Tuple[str, NoteUrl]]:
    if config.sync_dir is None:
        return
    for root, _, files in os.walk(config.sync_dir):
        for filename in files:
            fullpath = os.path.join(root, filename)
            url = parser.url_from_file(config, fullpath)
            if url is not None:
                yield (fullpath, url)


def load_file(
    api: KeepApi,
    config: Config,
    filename: str,
    url: NoteUrl,
) -> t.Optional[TopLevelNode]:
    note = api.get(url.id)
    if note is None:
        return None

    parser.parse(api, config, filename, note)
    if not note.title:
        note.title = url.title
    return note


def load_new_files(api: KeepApi, config: Config) -> t.Iterator[t.Tuple[str, str]]:
    if config.sync_dir is None:
        return
    logger.debug("Looking for new gkeep notes in %s", config.sync_dir)
    for filename, url in find_files(config):
        if url.id is None and api.get(url.id) is None:
            new_filename = create_note_from_file(api, config, filename, url)
            if new_filename is not None:
                yield (filename, new_filename)


def create_note_from_file(
    api: KeepApi, config: Config, filename: str, url: NoteUrl, file: parser.TFile = None
) -> t.Optional[str]:
    if file is None:
        file = filename
    lines = parser.read_lines(file)
    ntype = detect_note_type(filename, lines)
    logger.info("Creating new note from %s", filename)
    if ntype == NoteEnum.NOTE:
        note = Note()
    elif ntype == NoteEnum.LIST:
        note = List()
    else:
        raise ValueError(f"Unknown note type {ntype}")
    api.add(note)
    if url.id is not None:
        id = note.id = url.id
    else:
        id = url.id = note.id
    parser.parse(api, config, lines, note)
    if not note.title:
        title = note.title = url.title
    else:
        title = url.title = note.title
    # This shouldn't happen, since the url should have the title populated from the
    # file/buffer name, but it makes the type checking behave.
    if title is None:
        title = "Unknown"

    if isinstance(file, Buffer):
        parser.write_buffer_metadata(file, id, title)
    else:
        parser.write_file_metadata(filename, id, title, lines)
    new_filepath = url.filepath(api, config, note)
    if new_filepath is not None and new_filepath != filename:
        return new_filepath
    return None


class NoteFile(t.NamedTuple):
    url: NoteUrl
    bufname: str
    lines: t.Optional[t.Sequence[str]]

    @classmethod
    def from_note(cls, api: KeepApi, config: Config, note: TopLevelNode) -> "NoteFile":
        url = NoteUrl.from_note(note)
        bufname = url.bufname(api, config, note)
        if NoteUrl.is_ephemeral(bufname):
            lines = None
        else:
            lines = list(parser.serialize(config, note))
        return cls(url, bufname, lines)


def write_files(
    api: KeepApi,
    config: Config,
    protected_files: t.Container[str],
    notes: t.Sequence[NoteFile],
    updated_notes: t.Container[str],
) -> t.Dict[str, str]:
    if config.sync_dir is None:
        return {}
    renames = {}

    # Find all pre-existing note files
    files_by_id = {}
    for notefile, url in find_files(config):
        if url is not None and url.id:
            files_by_id[url.id] = notefile

    # Make notes directory if needed
    if not os.path.exists(config.sync_dir):
        os.makedirs(config.sync_dir, exist_ok=True)
    if config.archive_sync_dir and not os.path.exists(config.archive_sync_dir):
        os.makedirs(config.archive_sync_dir, exist_ok=True)

    for url, new_filepath, lines in notes:
        assert url.id is not None
        existing_file: t.Optional[str] = files_by_id.pop(url.id, None)
        if NoteUrl.is_ephemeral(new_filepath):
            if existing_file is not None and os.path.exists(existing_file):
                if existing_file in protected_files:
                    _soft_delete(existing_file)
                else:
                    logger.info("Deleting %s", existing_file)
                    os.unlink(existing_file)
                renames[existing_file] = new_filepath
            continue
        elif existing_file is not None and existing_file != new_filepath:
            renames[existing_file] = new_filepath
            # If the new filename is different, we should write to the existing file
            # here, and the move will be taken care of in the rename operation
            new_filepath = existing_file
        else:
            # It's possible that an ephemeral note has become a file note (due to
            # undeleting).  If that is the case, we should rename those buffers
            assert lines is not None
            renames[url.ephemeral_bufname(config, "\n".join(lines))] = new_filepath
        assert lines is not None

        if not os.path.exists(new_filepath):
            _write_file(new_filepath, lines, False)
        elif _hash_content(lines) != _hash_file(new_filepath):
            if url.id in updated_notes:
                _write_file(new_filepath, lines, new_filepath in protected_files)
            else:
                load_file(api, config, new_filepath, url)

    for oldfile in files_by_id.values():
        _soft_delete(oldfile)
    return renames


def _soft_delete(filename: str) -> None:
    logger.warning("Local file has remote changes. Moving to backup %s", filename)
    os.rename(filename, filename + ".local")


def _write_file(filename: str, lines: t.Iterable[str], keep_backup: bool) -> None:
    if os.path.exists(filename) and keep_backup:
        _soft_delete(filename)
    logger.info("Writing %s", filename)
    with open(filename, "w") as ofile:
        for line in lines:
            ofile.write(line)
            ofile.write("\n")


def _hash_file(filename: str) -> str:
    block_size = 65536
    md5 = hashlib.md5()
    with open(filename, "rb") as ifile:
        while True:
            data = ifile.read(block_size)
            if not data:
                return md5.hexdigest()
            md5.update(data)


def _hash_content(lines: t.Sequence[str]) -> str:
    md5 = hashlib.md5()
    for line in lines:
        md5.update(line.encode("utf-8"))
        md5.update(b"\n")
    return md5.hexdigest()
