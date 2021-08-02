import os
from pathlib import Path

import pytest
from gkeep import fssync, parser
from gkeep.api import KeepApi
from gkeep.config import Config
from gkeep.util import NoteUrl
from gkeepapi.node import List, Note

# pylint: disable=redefined-outer-name


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(tmp_path, sync_dir=tmp_path)


@pytest.fixture
def api() -> KeepApi:
    return KeepApi()


def write_note(api: KeepApi, config: Config, note: Note, strip_id=False) -> str:
    fname = NoteUrl.from_note(note).filepath(api, config, note)
    assert fname is not None
    lines = parser.serialize(config, note)
    if strip_id:
        lines = [l for l in lines if not l.startswith("id:")]
    fssync._write_file(fname, lines, False)
    return fname


def test_write_notes(api: KeepApi, config: Config) -> None:
    """Write notes to empty directory"""
    note = Note()
    note.title = "My Note"
    nf = fssync.NoteFile.from_note(api, config, note)
    renames = fssync.write_files(api, config, [], [nf], [])
    url = NoteUrl.from_note(note)
    fname = url.filepath(api, config, note)
    assert renames == {
        url.ephemeral_bufname(config, note): fname
    }, "Attempt to rename the ephemeral buffer if it exists"
    assert fname is not None
    assert os.path.exists(fname), "The note should be written to a file"


def test_no_write_trashed_notes(api: KeepApi, config: Config) -> None:
    """Don't write trashed notes to directory"""
    note = Note()
    note.title = "My Note"
    note.trash()
    nf = fssync.NoteFile.from_note(api, config, note)
    renames = fssync.write_files(api, config, [], [nf], [])
    assert renames == {}
    fname = NoteUrl.from_note(note).filepath(api, config, note)
    assert fname is None
    assert config.sync_dir is not None
    filepath = os.path.join(config.sync_dir, note.title + ".keep")
    assert not os.path.exists(filepath)


def test_soft_delete_unknown_notes(api: KeepApi, config: Config) -> None:
    """Note files with id, but no matching note, get soft deleted"""
    note = Note()
    note.title = "My Note"
    fname = write_note(api, config, note)
    renames = fssync.write_files(api, config, [], [], [])
    assert renames == {}
    assert not os.path.exists(fname)
    assert os.path.exists(fssync.get_local_file(fname))


def test_update_existing(api: KeepApi, config: Config) -> None:
    """Existing note files are updated if not protected"""
    note = Note()
    note.title = "My Note"
    fname = write_note(api, config, note)
    note.text = "Some text"
    nf = fssync.NoteFile.from_note(api, config, note)
    renames = fssync.write_files(api, config, [], [nf], [note.id])
    assert renames == {}
    assert os.path.exists(fname), "The note file should still exist"
    new_note = Note()
    parser.parse(api, config, fname, new_note)
    assert new_note.title == "My Note", "Note file title should still match"
    assert new_note.text == "Some text", "Note file text should be updated"


def test_no_update_existing(api: KeepApi, config: Config) -> None:
    """Existing note files are not updated if id not in the updated list"""
    note = Note()
    note.title = "My Note"
    note.text = "Initial text"
    fname = write_note(api, config, note)
    note.text = "Some text"
    nf = fssync.NoteFile.from_note(api, config, note)
    renames = fssync.write_files(api, config, [], [nf], [])
    assert renames == {}
    assert os.path.exists(fname), "The note file should still exist"
    new_note = Note()
    parser.parse(api, config, fname, new_note)
    assert new_note.title == "My Note", "Note file title should still match"
    assert new_note.text == "Initial text", "Note file text should NOT be updated"


def test_protect_existing(api: KeepApi, config: Config) -> None:
    """Existing note files, if protected, are soft deleted on conflict"""
    note = Note()
    note.title = "My Note"
    note.text = "Initial text"
    fname = write_note(api, config, note)
    note.text = "Some text"
    nf = fssync.NoteFile.from_note(api, config, note)
    renames = fssync.write_files(api, config, [fname], [nf], [note.id])
    assert renames == {}
    assert os.path.exists(fname), "The note file should still exist"
    local = fssync.get_local_file(fname)
    assert os.path.exists(local), "The original note should be renamed"
    new_note = Note()
    parser.parse(api, config, fname, new_note)
    assert new_note.text == "Some text", "Note file text should be updated"
    new_note = Note()
    parser.parse(api, config, local, new_note)
    assert (
        new_note.text == "Initial text"
    ), "Renamed note file text should be the original content"


def test_rename_file(api: KeepApi, config: Config) -> None:
    """Note file is renamed if title changes"""
    note = Note()
    note.title = "My Note"
    fname = write_note(api, config, note)
    note.title = "New Note"
    new_name = NoteUrl.from_note(note).filepath(api, config, note)
    assert new_name is not None
    nf = fssync.NoteFile.from_note(api, config, note)
    renames = fssync.write_files(api, config, [], [nf], [note.id])
    assert renames == {fname: new_name}


def test_delete_file(api: KeepApi, config: Config) -> None:
    """Files that were trashed are deleted from disk"""
    note = Note()
    note.title = "My Note"
    fname = write_note(api, config, note)
    note.trash()
    nf = fssync.NoteFile.from_note(api, config, note)
    renames = fssync.write_files(api, config, [], [nf], [note.id])
    url = NoteUrl.from_note(note)
    assert renames == {fname: url.ephemeral_bufname(config, note)}
    assert not os.path.exists(fname)


def test_protect_deleted(api: KeepApi, config: Config) -> None:
    """Deleted notes that are protected are moved to local"""
    note = Note()
    note.title = "My Note"
    fname = write_note(api, config, note)
    note.trash()
    nf = fssync.NoteFile.from_note(api, config, note)
    renames = fssync.write_files(api, config, [fname], [nf], [note.id])
    url = NoteUrl.from_note(note)
    assert renames == {fname: url.ephemeral_bufname(config, note)}
    assert not os.path.exists(fname)
    assert os.path.exists(fssync.get_local_file(fname))


def test_special_chars(api: KeepApi, config: Config) -> None:
    """Can sync notes with special chars in the title"""
    note = Note()
    note.title = r"My Note\/!@#$%^&*()💗"
    nf = fssync.NoteFile.from_note(api, config, note)
    renames = fssync.write_files(api, config, [], [nf], [])
    url = NoteUrl.from_note(note)
    fname = url.filepath(api, config, note)
    assert renames == {
        url.ephemeral_bufname(config, note): fname
    }, "Attempt to rename the ephemeral buffer if it exists"
    assert fname is not None
    assert os.path.exists(fname), "The note should be written to a file"


def test_load_new_files(api: KeepApi, config: Config) -> None:
    """Can create notes from new files"""
    note = Note()
    note.title = "My Note"
    write_note(api, config, note, strip_id=True)
    renames = fssync.load_new_files(api, config)
    assert renames == {}
    notes = list(api.all())
    assert len(notes) == 1
    assert notes[0].title == "My Note"
    assert (
        notes[0].id != note.id
    ), "The new note id should be different from the one we used to create the file"


def test_new_raw_file(api: KeepApi, config: Config) -> None:
    """Can create new notes even if file format is totally wrong"""
    assert config.sync_dir is not None
    fname = os.path.join(config.sync_dir, "testnote.keep")
    with open(fname, "w") as ofile:
        ofile.write("[ ] item one\n")
    renames = fssync.load_new_files(api, config)
    assert renames == {}
    notes = list(api.all())
    assert len(notes) == 1
    note = notes[0]
    assert note.title == "testnote", "New note should take title from filename"
    assert isinstance(note, List), "This note was formatted as a list"
    assert len(note.items) == 1
    assert note.items[0].text == "item one"
