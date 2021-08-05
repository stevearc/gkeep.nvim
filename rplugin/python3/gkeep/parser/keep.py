import logging
import re
import typing as t

from gkeep import util
from gkeep.api import KeepApi
from gkeep.parser.common import Header, TFile, merge_labels, read_lines
from gkeep.util import NoteEnum
from gkeepapi.node import Label, List, ListItem, Note, TopLevelNode
from pynvim.api import Buffer

logger = logging.getLogger(__name__)

LABEL_RE = re.compile(r'"([^"]+)"|([^,"]+)')
SPACE_RE = re.compile(r"^\s*$")
LIST_ITEM_RE = re.compile(r"^(\s*)\[([ x\-])\]\s*(.*)$", re.I)

__all__ = ["serialize", "parse", "detect_note_type", "toggle_list_item"]


def detect_note_type(filename: str, file: TFile) -> NoteEnum:
    ext = util.get_ext(filename)
    if ext != ".keep":
        return NoteEnum.NOTE
    lines = read_lines(file, 8)
    for line in lines:
        if LIST_ITEM_RE.match(line):
            return NoteEnum.LIST
    return NoteEnum.NOTE


def toggle_list_item(line: str) -> str:
    match = LIST_ITEM_RE.match(line)
    if not match:
        return line
    checked = match[2].lower() == "x"
    newcheck = " " if checked else "x"
    return LIST_ITEM_RE.sub(f"\\1[{newcheck}] \\3", line)


def serialize(note: TopLevelNode) -> t.Iterator[str]:
    yield from _gen_header(note)
    if isinstance(note, Note):
        yield from _gen_note_body(note)
    elif isinstance(note, List):
        yield from _gen_list_body(note)
    else:
        raise ValueError(f"Unknown note type {type(note)}")


def _gen_header(note: TopLevelNode) -> t.Iterator[str]:
    yield f"# {note.title.strip()}"
    yield f"id: {note.id}"
    labels = []
    for label in note.labels.all():
        if "," in label.name:
            labels.append(f'"{label.name}"')
        else:
            labels.append(label.name)
    if labels:
        yield "labels: " + ", ".join(labels)
    yield ""


def _gen_note_body(note: "Note") -> t.Iterator[str]:
    yield from note.text.split("\n")


def _gen_list_body(note: "List") -> t.Iterator[str]:
    for item in note.items:
        prefix = "    " if item.indented else ""
        checked = item.checked
        if not item.indented and not checked:
            for child in item.subitems:
                if child.checked:
                    checked = "partial"
        yield f"{prefix}{util.checkbox(checked)}{item.text}"


def _sync_header(note: TopLevelNode, header: Header, labels: t.Sequence[Label]) -> None:
    if note.title != header.title:
        note.title = header.title
    if note.id != header.id and header.id is not None:
        note.id = header.id
    merge_labels(note, labels)


def parse(api: KeepApi, lines: t.Sequence[str], note: TopLevelNode) -> None:
    header, labels, i = _parse_header(api, lines)
    _sync_header(note, header, labels)
    if isinstance(note, Note):
        parse_note_body(lines, note, i)
    elif isinstance(note, List):
        _parse_list_body(lines, note, i)
    else:
        raise ValueError(f"Unknown note type {type(note)}")


def _parse_labels(api: KeepApi, labels_str: str) -> t.List[Label]:
    labels = []
    for match in LABEL_RE.finditer(labels_str):
        label_str = (match[1] or match[2]).strip()
        label = api.findLabel(label_str)
        if label is not None:
            labels.append(label)
    return labels


def _parse_meta(lines: t.Sequence[str]) -> t.Tuple[Header, int]:
    title = None
    id = None
    i = 0
    if lines and lines[i].startswith("#"):
        title = lines[i][1:].strip()
        i += 1
    if i < len(lines) and lines[i].startswith("id:"):
        id = lines[i][len("id:") :].strip()
        i += 1
    return Header(id, title), i


def _parse_header(
    api: KeepApi, lines: t.Sequence[str]
) -> t.Tuple[Header, t.Sequence[Label], int]:
    header, i = _parse_meta(lines)
    labels = []
    if i < len(lines) and lines[i].startswith("labels:"):
        label_line = lines[i][len("labels:") :]
        labels = _parse_labels(api, label_line)
        i += 1

    if i < len(lines) and lines[i].strip() == "":
        i += 1
    return header, labels, i


def parse_note_body(lines: t.Sequence[str], note: Note, i: int) -> None:
    text = "\n".join(lines[i:])
    if text != note.text:
        note.text = text


def get_metadata(file: TFile) -> Header:
    lines = list(read_lines(file, 5))
    return _parse_meta(lines)[0]


def _write_meta(lines: t.List[str], header: Header, id: str, title: str) -> None:
    if header.title is None:
        lines.insert(0, f"# {title}")
    elif header.title != title:
        lines[0] = f"# {title}"
    if header.id is None:
        lines.insert(1, f"id: {id}")
    elif header.id != id:
        lines[1] = f"id: {id}"


def write_file_meta(filename: str, id: str, title: str, file: TFile = None) -> None:
    if file is None:
        file = filename
    lines = list(read_lines(file))
    header = _parse_meta(lines)[0]
    _write_meta(lines, header, id, title)
    with open(filename, "w") as ofile:
        for line in lines:
            ofile.write(line)
            ofile.write("\n")


def write_buffer_meta(buffer: Buffer, id: str, title: str) -> None:
    lines = buffer[:4]
    header, i = _parse_meta(lines)
    _write_meta(lines, header, id, title)
    buffer[0:i] = lines[0:2]


class ListItemDict:
    def __init__(self, items: t.Optional[t.Iterable[ListItem]]):
        self._next_key = 0
        self._map: t.Dict[str, t.List[ListItem]] = {}
        if items is not None:
            for item in items:
                self.add(item)

    def __iter__(self) -> t.Iterator[ListItem]:
        for val in self._map.values():
            yield from val

    def add(self, item: ListItem) -> None:
        text = item.text
        if text not in self._map:
            self._map[text] = [item]
        else:
            self._map[text].append(item)

    def pop(self, text: str) -> t.Optional[ListItem]:
        if text not in self._map:
            return None
        values = self._map[text]
        item = values.pop(0)
        if not values:
            del self._map[text]
        return item


def _dedent(item: ListItem) -> None:
    assert item.parent_item is not None
    # Manually set super_list_item_id because sometimes the child item
    # *isn't* in the subitems of the parent, leading to a silent failure.
    # Don't know why this happens.
    item.parent_item.dedent(item)
    item.super_list_item_id = ""
    item.touch(True)


def _indent(item: ListItem, parent: ListItem) -> None:
    for subitem in item.subitems:
        _dedent(subitem)
    parent.indent(item)


def _parse_list_body(lines: t.Sequence[str], note: List, start: int) -> None:
    items = ListItemDict(note.items)

    parent: t.Optional[ListItem] = None
    new_items = []
    last_sort: t.Optional[int] = None
    resort = False
    for i in range(start, len(lines)):
        line = lines[i]
        match = LIST_ITEM_RE.match(line)
        if match:
            indented = bool(match[1])
            checked = match[2].lower() == "x"
            text = match[3]
        elif SPACE_RE.match(line):
            continue
        else:
            indented = line.startswith(" ")
            checked = False
            text = line.strip()

        if indented:
            # If the first element is indented, silently dedent it
            if parent is None:
                indented = False
        item = items.pop(text)
        if item is None:
            if last_sort is not None:
                sort = last_sort - 1000000
            else:
                sort = 0
            item = note.add(text, checked, sort)

        if indented:
            assert parent is not None
            checked = checked or parent.checked

        if last_sort is not None and int(item.sort) >= last_sort:
            resort = True
        last_sort = int(item.sort)
        if item.text != text:
            item.text = text
        if item.checked != checked:
            item.checked = checked
        if item.indented != indented:
            if indented:
                assert parent is not None
                _indent(item, parent)
            else:
                assert item.parent_item is not None
                _dedent(item)
        elif (
            indented
            and parent is not None
            and parent.id != getattr(item.parent_item, "id", None)
        ):
            _dedent(item)
            parent.indent(item)

        new_items.append(item)
        if not indented:
            parent = item

    if resort:
        sort = 0
        for item in new_items:
            if int(item.sort) != sort:
                item.sort = sort
            sort -= 1000000

    for missing in items:
        missing.delete()
