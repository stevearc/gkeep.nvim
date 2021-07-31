import getpass
import typing as t
from datetime import datetime
from pathlib import Path

import pytest
from gkeep import parser
from gkeep.config import Config
from gkeep.parser import keep, neorg
from gkeep.util import NoteEnum
from gkeepapi.node import Label, List, Note

# pylint: disable=redefined-outer-name


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(tmp_path)


@pytest.fixture
def labels() -> t.List[Label]:
    ret = []
    for name in ["OneWord", "Two Words", "Has, Comma"]:
        label = Label()
        label.name = name
        ret.append(label)
    return ret


@pytest.fixture
def note(labels: t.List[Label]) -> Note:
    note = Note()
    note.title = "My Note"
    note.timestamps.created = datetime(1999, 4, 1)
    for label in labels:
        note.labels.add(label)
    note.text = "some text"
    return note


@pytest.fixture
def notelist(labels: t.List[Label]) -> List:
    note = List()
    note.title = "My List"
    note.timestamps.created = datetime(1999, 4, 1)
    for label in labels:
        note.labels.add(label)
    note.add("first item", True, 10)
    note.add("second item", False, 5)
    n1 = note.add("parent item", False, 0)
    n2 = note.add("sub item", True, 100)
    n1.indent(n2)
    return note


def test_serialize_note(config: Config, note: Note) -> None:
    """Serialize simple note"""
    text = "\n".join(parser.serialize(config, note))
    assert (
        text
        == f"""# My Note
id: {note.id}
labels: OneWord, Two Words, "Has, Comma"

some text"""
    )


def test_serialize_list(config: Config, notelist: List) -> None:
    """Serialize simple list"""
    text = "\n".join(parser.serialize(config, notelist))
    assert (
        text
        == f"""# My List
id: {notelist.id}
labels: OneWord, Two Words, "Has, Comma"

[x] first item
[ ] second item
[-] parent item
    [x] sub item"""
    )


def test_serialize_neorg(config: Config, note: Note) -> None:
    label = Label()
    label.name = "Label"
    text = "\n".join(parser.serialize(config, note, "norg"))
    author = getpass.getuser()
    assert (
        text
        == f"""@document.meta
\ttitle: My Note
\tdescription:
\tauthor: {author}
\tcategories: OneWord Two Words Has, Comma
\tcreated: 1999-04-01
\tversion: 0.1
\tgkeep: {note.id}
@end

some text"""
    )


def test_detect_note() -> None:
    nt = keep.detect_note_type(
        "test.keep",
        [
            "# Title",
            "labels: Foo, Bar, Baz",
            "This is some text",
            "And some more text",
        ],
    )
    assert nt == NoteEnum.NOTE


def test_detect_list() -> None:
    nt = keep.detect_note_type(
        "test.keep",
        [
            "# Title",
            "labels: Foo, Bar, Baz",
            "[ ] here is an item",
        ],
    )
    assert nt == NoteEnum.LIST


def test_detect_neorg() -> None:
    nt = keep.detect_note_type(
        "test.norg",
        [
            "@document.meta",
            "\ttitle: My note",
            "@end",
        ],
    )
    assert nt == NoteEnum.NOTE
