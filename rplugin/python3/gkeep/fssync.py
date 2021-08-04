import hashlib
import logging
import os
import typing as t
from abc import ABC, abstractmethod
from datetime import datetime

import gkeep.parser as parser
from gkeep.api import KeepApi
from gkeep.config import Config, State
from gkeep.parser.keep import detect_note_type
from gkeep.status import status
from gkeep.util import NoteEnum, NoteUrl
from gkeepapi.node import List, Note, TopLevelNode
from pynvim.api import Buffer

logger = logging.getLogger(__name__)


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


class ISync(ABC):
    @abstractmethod
    def start(self, state: t.Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def finish_startup(self, updated_notes: t.Container[str]) -> t.Dict[str, str]:
        """
        Resolve any changes needed for initial sync

        Will be run in background thread, but should have exclusive control over KeepApi
        """
        raise NotImplementedError

    @abstractmethod
    def write_files(self, updated_notes: t.Sequence[NoteFile]) -> t.Dict[str, str]:
        """
        Write any necessary file changes to disk

        Will run in background thread, and should not touch the KeepApi
        """
        raise NotImplementedError


class NoopSync(ISync):
    def __init__(self, api: KeepApi):
        self._api = api

    def start(self, state: t.Any) -> None:
        restore_state(self._api, state)

    def finish_startup(self, updated_notes: t.Container[str]) -> t.Dict[str, str]:
        return {}

    def write_files(self, updated_notes: t.Sequence[NoteFile]) -> t.Dict[str, str]:
        return {}


@status("Loading cache")
def restore_state(api: KeepApi, state: t.Any) -> None:
    if state is not None:
        with status("Loading cache"):
            api.restore(state)


class FileSync(ISync):
    def __init__(self, api: KeepApi, config: Config):
        self._api = api
        self._config = config
        self._protected_files: t.Set[str] = set()

    def start(self, state: t.Any) -> None:
        assert self._config.state == State.Uninitialized
        restore_state(self._api, state)
        if state is None:
            for filename, _ in _find_files(self._config):
                self._protected_files.add(filename)
        else:
            with status("Reading note files"):
                self._protected_files.update(self._find_files_with_changes())

    def finish_startup(self, updated_notes: t.Container[str]) -> t.Dict[str, str]:
        assert self._config.state == State.InitialSync
        with status("Writing note files"):
            renamed_files, need_load = _write_files(
                self._config,
                self._protected_files,
                [
                    NoteFile.from_note(self._api, self._config, n)
                    for n in self._api.all()
                ],
                updated_notes,
                True,
            )
        with status("Loading changed files"):
            for filename in need_load:
                logger.warning(
                    "Changes detected in %s. Backing up Google Keep note",
                    filename,
                )
                url = parser.url_from_file(self._config, filename)
                assert url is not None
                note = self._api.get(url.id)
                assert note is not None
                backup = _make_backup(note)
                self._api.add(backup)
                self._load_file(filename, url)
        with status("Loading new note files"):
            renamed_files.update(self._load_new_files())
        self._protected_files.clear()
        return renamed_files

    def write_files(self, updated_notes: t.Sequence[NoteFile]) -> t.Dict[str, str]:
        assert self._config.state == State.Running
        renames, _ = _write_files(
            self._config,
            set(),
            updated_notes,
            {nf.url.id for nf in updated_notes if nf.url.id is not None},
            False,
        )
        return renames

    def _load_new_files(self) -> t.Dict[str, str]:
        ret = {}
        logger.debug("Looking for new gkeep notes in %s", self._config.sync_dir)
        for filename, url in _find_files(self._config):
            if url.id is None and self._api.get(url.id) is None:
                new_filename = create_note_from_file(
                    self._api, self._config, filename, url
                )
                if new_filename is not None:
                    ret[filename] = new_filename
        return ret

    def _find_files_with_changes(self) -> t.List[str]:
        state = self._api.dump()
        logger.debug("Loading gkeep files from %s", self._config.sync_dir)
        ret = []
        for filename, url in _find_files(self._config):
            note = self._load_file(filename, url)
            if note is None or note.dirty:
                ret.append(filename)
        # Clear the dirty state from reading in files.
        # We want this first sync to simply update our internal state, and
        # *then* we will process any changes in the note files.
        self._api.restore(state)
        return ret

    def _load_file(
        self,
        filename: str,
        url: NoteUrl,
    ) -> t.Optional[TopLevelNode]:
        note = self._api.get(url.id)
        if note is None:
            return None

        parser.parse(self._api, self._config, filename, note)
        if not note.title:
            note.title = url.title
        return note


def _find_files(config: Config) -> t.Iterator[t.Tuple[str, NoteUrl]]:
    if config.sync_dir is None:
        return
    for root, _, files in os.walk(config.sync_dir):
        for filename in files:
            fullpath = os.path.join(root, filename)
            url = parser.url_from_file(config, fullpath)
            if url is not None:
                yield (fullpath, url)


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


def _make_backup(note: TopLevelNode) -> TopLevelNode:
    if isinstance(note, Note):
        backup = Note()
        backup.text = note.text
    elif isinstance(note, List):
        backup = List()
        map = {}
        for item in note.items:
            new_item = backup.add(item.text, item.checked, item.sort)
            map[item.id] = new_item
            if item.indented:
                map[item.parent_item.id].indent(new_item)
    else:
        raise NotImplementedError
    backup.trash()
    backup.title = f"[Backup {datetime.now().date()}] {note.title}"
    return backup


def _write_files(
    config: Config,
    protected_files: t.Container[str],
    notes: t.Sequence[NoteFile],
    updated_notes: t.Container[str],
    initial_load: bool,
) -> t.Tuple[t.Dict[str, str], t.Sequence[str]]:
    """
    Write updated files to disk, resolving merge conflicts

    This intentionally does not use KeepApi because it needs to be able to run in a
    background thread while the main thread can still make changes to notes.
    """
    if config.sync_dir is None:
        return {}, []
    renames = {}
    need_load = []

    # Find all pre-existing note files
    files_by_id = {}
    for notefile, url in _find_files(config):
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
        elif existing_file is None:
            # It's possible that an ephemeral note has become a file note (due to
            # undeleting).  If that is the case, we should rename those buffers
            assert lines is not None
            renames[url.ephemeral_bufname(config, "\n".join(lines))] = new_filepath

        assert lines is not None
        if not os.path.exists(new_filepath):
            _write_file(new_filepath, lines, False)
        elif url.id in updated_notes and _content_changed(lines, new_filepath):
            _write_file(new_filepath, lines, new_filepath in protected_files)
        elif initial_load and _content_changed(lines, new_filepath):
            need_load.append(new_filepath)

    if initial_load:
        for oldfile in files_by_id.values():
            _soft_delete(oldfile)
    return renames, need_load


def _soft_delete(filename: str) -> None:
    logger.warning("Local file has remote changes. Moving to backup %s", filename)
    os.rename(filename, get_local_file(filename))


def get_local_file(filename: str) -> str:
    return filename + ".local"


def _write_file(filename: str, lines: t.Iterable[str], keep_backup: bool) -> None:
    if os.path.exists(filename) and keep_backup:
        _soft_delete(filename)
    logger.info("Writing %s", filename)
    with open(filename, "w", encoding="utf-8") as ofile:
        for line in lines:
            ofile.write(line)
            ofile.write("\n")


def _content_changed(lines: t.Sequence[str], filename: str) -> bool:
    return _hash_content(lines) != _hash_file(filename)


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
