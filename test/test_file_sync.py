import os
import typing as t
from copy import deepcopy
from pathlib import Path

import pytest
from freezegun import freeze_time
from gkeep import parser
from gkeep.api import KeepApi
from gkeep.config import Config, State
from gkeep.fssync import FileSync, NoteFile, _write_file, get_local_file
from gkeep.util import NoteUrl
from gkeepapi.node import List, Note, TopLevelNode

# pylint: disable=redefined-outer-name


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(tmp_path, sync_dir=tmp_path)


@pytest.fixture
def api() -> KeepApi:
    return KeepApi()


@pytest.fixture
def fsync(api: KeepApi, config: Config) -> FileSync:
    return FileSync(api, config)


def finish_startup(fsync: FileSync, config: Config) -> None:
    fsync.start(None)
    config.state = State.InitialSync
    fsync.finish_startup(set())
    config.state = State.Running


def write_note(
    api: KeepApi,
    config: Config,
    note: TopLevelNode,
    text: t.Optional[str] = None,
    strip_id: bool = False,
) -> str:
    fname = NoteUrl.from_note(note).filepath(api, config, note)
    assert fname is not None
    if text is not None:
        note = deepcopy(note)
        note.text = text
    lines = list(parser.serialize(config, note))
    if strip_id:
        lines = [l for l in lines if not l.startswith("id:")]
    _write_file(fname, lines, False)
    return fname


def test_write_note(api: KeepApi, config: Config, fsync: FileSync) -> None:
    """Write note to empty directory"""
    finish_startup(fsync, config)
    note = Note()
    note.title = "My Note"
    nf = NoteFile.from_note(api, config, note)
    renames = fsync.write_files([nf])
    url = NoteUrl.from_note(note)
    fname = url.filepath(api, config, note)
    assert renames == {
        url.ephemeral_bufname(config, note): fname
    }, "Attempt to rename the ephemeral buffer if it exists"
    assert fname is not None
    assert os.path.exists(fname), "The note should be written to a file"


def test_no_write_trashed_notes(api: KeepApi, config: Config, fsync: FileSync) -> None:
    """Don't write trashed notes to directory"""
    finish_startup(fsync, config)
    note = Note()
    note.title = "My Note"
    note.trash()
    nf = NoteFile.from_note(api, config, note)
    renames = fsync.write_files([nf])
    assert renames == {}
    fname = NoteUrl.from_note(note).filepath(api, config, note)
    assert fname is None
    assert config.sync_dir is not None
    filepath = os.path.join(config.sync_dir, note.title + ".keep")
    assert not os.path.exists(filepath)


def test_soft_delete_unknown_notes(
    api: KeepApi, config: Config, fsync: FileSync
) -> None:
    """Note files with id, but no matching note, get soft deleted"""
    fsync.start(None)
    config.state = State.InitialSync
    note = Note()
    note.title = "My Note"
    fname = write_note(api, config, note)
    renames = fsync.finish_startup([])
    assert renames == {}
    assert not os.path.exists(fname)
    assert os.path.exists(get_local_file(fname))


def test_update_existing(api: KeepApi, config: Config, fsync: FileSync) -> None:
    """Existing note files are updated if no local changes"""
    fsync.start(None)
    config.state = State.InitialSync
    note = Note()
    note.title = "My Note"
    fname = write_note(api, config, note)
    note.text = "Some text"
    api.add(note)
    renames = fsync.finish_startup([note.id])
    assert renames == {}
    assert os.path.exists(fname), "The note file should still exist"
    new_note = Note()
    parser.parse(api, config, fname, new_note)
    assert new_note.title == "My Note", "Note file title should still match"
    assert new_note.text == "Some text", "Note file text should be updated"


def test_no_update_existing(api: KeepApi, config: Config, fsync: FileSync) -> None:
    """Existing note files are not updated if id not in the updated list"""
    fsync.start(None)
    config.state = State.InitialSync
    note = Note()
    note.title = "My Note"
    note.text = "Initial text"
    fname = write_note(api, config, note)
    note.text = "Some text"
    api.add(note)
    nf = NoteFile.from_note(api, config, note)
    renames = fsync.finish_startup([])
    assert renames == {}
    assert os.path.exists(fname), "The note file should still exist"
    new_note = Note()
    parser.parse(api, config, fname, new_note)
    assert new_note.title == "My Note", "Note file title should still match"
    assert new_note.text == "Initial text", "Note file text should NOT be updated"


def test_protected_unknown(api: KeepApi, config: Config, fsync: FileSync) -> None:
    """If no cache state, files are soft deleted instead of updated"""
    note = Note()
    note.title = "My Note"
    note.text = "Mem text"
    fname = write_note(api, config, note, text="File text")
    api.add(note)
    fsync.start(None)
    config.state = State.InitialSync
    note.text = "New text"
    renames = fsync.finish_startup([note.id])
    assert renames == {}
    assert os.path.exists(fname), "The note file should still exist"
    local = get_local_file(fname)
    assert os.path.exists(local), "The original note should be renamed"
    new_note = Note()
    parser.parse(api, config, fname, new_note)
    assert new_note.text == "New text", "Note file text should be updated"
    new_note = Note()
    parser.parse(api, config, local, new_note)
    assert (
        new_note.text == "File text"
    ), "Renamed note file text should be the original content"


def test_protect_existing(api: KeepApi, config: Config, fsync: FileSync) -> None:
    """Existing note files with changes from cache are soft deleted on conflict"""
    note = Note()
    note.title = "My Note"
    note.text = "Mem text"
    fname = write_note(api, config, note, text="File text")
    api.add(note)
    state = api.dump()
    fsync.start(state)
    config.state = State.InitialSync
    note = api.get(note.id)
    note.text = "New text"
    renames = fsync.finish_startup([note.id])
    assert renames == {}
    assert os.path.exists(fname), "The note file should still exist"
    local = get_local_file(fname)
    assert os.path.exists(local), "The original note should be renamed"
    new_note = Note()
    parser.parse(api, config, fname, new_note)
    assert new_note.text == "New text", "Note file text should be updated"
    new_note = Note()
    parser.parse(api, config, local, new_note)
    assert (
        new_note.text == "File text"
    ), "Renamed note file text should be the original content"


def test_rename_file(api: KeepApi, config: Config, fsync: FileSync) -> None:
    """Note file is renamed if title changes"""
    finish_startup(fsync, config)
    note = Note()
    note.title = "My Note"
    fname = write_note(api, config, note)
    note.title = "New Note"
    new_name = NoteUrl.from_note(note).filepath(api, config, note)
    assert new_name is not None
    nf = NoteFile.from_note(api, config, note)
    renames = fsync.write_files([nf])
    assert renames == {fname: new_name}


def test_delete_file(api: KeepApi, config: Config, fsync: FileSync) -> None:
    """Files that were trashed are deleted from disk"""
    finish_startup(fsync, config)
    note = Note()
    note.title = "My Note"
    fname = write_note(api, config, note)
    note.trash()
    nf = NoteFile.from_note(api, config, note)
    renames = fsync.write_files([nf])
    url = NoteUrl.from_note(note)
    assert renames == {fname: url.ephemeral_bufname(config, note)}
    assert not os.path.exists(fname)


def test_protect_deleted(api: KeepApi, config: Config, fsync: FileSync) -> None:
    """Deleted notes that are protected are moved to local"""
    note = Note()
    note.title = "My Note"
    api.add(note)
    fname = write_note(api, config, note)
    fsync.start(None)
    config.state = State.InitialSync
    note.trash()
    renames = fsync.finish_startup([note.id])
    url = NoteUrl.from_note(note)
    assert renames == {fname: url.ephemeral_bufname(config, note)}
    assert not os.path.exists(fname)
    assert os.path.exists(get_local_file(fname))


def test_special_chars(api: KeepApi, config: Config, fsync: FileSync) -> None:
    """Can sync notes with special chars in the title"""
    finish_startup(fsync, config)
    note = Note()
    note.title = r"My Note\/!@#$%^&*()💗"
    nf = NoteFile.from_note(api, config, note)
    renames = fsync.write_files([nf])
    url = NoteUrl.from_note(note)
    fname = url.filepath(api, config, note)
    assert renames == {
        url.ephemeral_bufname(config, note): fname
    }, "Attempt to rename the ephemeral buffer if it exists"
    assert fname is not None
    assert os.path.exists(fname), "The note should be written to a file"


def test_load_new_files(api: KeepApi, config: Config, fsync: FileSync) -> None:
    """Can create notes from new files"""
    note = Note()
    note.title = "My Note"
    write_note(api, config, note, strip_id=True)
    fsync.start(None)
    config.state = State.InitialSync
    renames = fsync.finish_startup([])
    assert renames == {}
    notes = list(api.all())
    assert len(notes) == 1
    assert notes[0].title == "My Note"
    assert (
        notes[0].id != note.id
    ), "The new note id should be different from the one we used to create the file"


def test_new_raw_file(api: KeepApi, config: Config, fsync: FileSync) -> None:
    """Can create new notes even if file content is missing header"""
    assert config.sync_dir is not None
    fname = os.path.join(config.sync_dir, "testnote.keep")
    with open(fname, "w") as ofile:
        ofile.write("[ ] item one\n")
    fsync.start(None)
    config.state = State.InitialSync
    renames = fsync.finish_startup([])
    assert renames == {}
    notes = list(api.all())
    assert len(notes) == 1
    note = notes[0]
    assert note.title == "testnote", "New note should take title from filename"
    assert isinstance(note, List), "This note was formatted as a list"
    assert len(note.items) == 1
    assert note.items[0].text == "item one"


@freeze_time("2021-04-05")
def test_backup_changed_note(api: KeepApi, config: Config, fsync: FileSync) -> None:
    """If changes to file on disk would update note, back up note first"""
    note = Note()
    note.title = "My Note"
    note.text = "Mem text"
    api.add(note)
    write_note(api, config, note, text="File text")
    state = api.dump()
    fsync.start(state)
    config.state = State.InitialSync
    note = api.get(note.id)
    renames = fsync.finish_startup([])
    assert renames == {}
    assert note.text == "File text", "Note text should be updated from file"
    backups = [n for n in api.all() if n.id != note.id]
    assert len(backups) == 1
    backup = backups[0]
    assert backup.title == "[Backup 2021-04-05] My Note"
    assert backup.text == "Mem text", "Backup should have original note text"
    assert backup.trashed, "Backups go to the trash"
    assert backup.dirty, "Backups will need to be synced"

    # Reload state to simulate a sync()
    state = api.dump()
    api.restore(state)
    note = api.get(note.id)
    backup = api.get(backup.id)
    backup.text = "FOO"
    assert note.text == "File text", "Changing backup should not affect original"


@freeze_time("2021-04-05")
def test_backup_changed_list(api: KeepApi, config: Config, fsync: FileSync) -> None:
    """If changes to file on disk would update note, back up note first"""
    note = List()
    note.title = "My Note"
    parent = note.add("Parent item", False, 10)
    child = note.add("Child item", True, 0)
    parent.indent(child)
    api.add(note)
    fname = write_note(api, config, note)
    with open(fname, "a") as ofile:
        ofile.write("[ ] New item\n")
    state = api.dump()
    fsync.start(state)
    config.state = State.InitialSync
    note = api.get(note.id)
    renames = fsync.finish_startup([])
    assert renames == {}
    assert len(note.items) == 3, "Note items should be updated from file"
    backups = [n for n in api.all() if n.id != note.id]
    assert len(backups) == 1
    backup = backups[0]
    assert backup.title == "[Backup 2021-04-05] My Note"
    parent, child, new = note.items
    assert parent.text == "Parent item"
    assert not parent.checked
    assert not parent.indented
    assert child.text == "Child item"
    assert child.checked
    assert child.indented
    assert new.text == "New item"
    assert not new.checked
    assert not new.indented
    assert backup.trashed, "Backups go to the trash"
    assert backup.dirty, "Backups will need to be synced"

    # Reload state to simulate a sync()
    state = api.dump()
    api.restore(state)
    note = api.get(note.id)
    backup = api.get(backup.id)
    first = backup.items[0]
    first.text = "FOO"
    assert (
        note.items[0].text == "Parent item"
    ), "Changing backup should not affect original"
