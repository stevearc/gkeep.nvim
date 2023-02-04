import getpass
import logging
import re
import typing as t

from gkeep.api import KeepApi
from gkeep.parser.common import Header, TFile, merge_labels, read_lines
from gkeepapi.node import Label, Note
from pynvim.api import Buffer

META_LINE_RE = re.compile(r"^\s*@document.meta\s*$")
META_RE = re.compile(r"^\s*@document.meta\b")
END_RE = re.compile(r"^\s*@end\s*$")

logger = logging.getLogger(__name__)


def has_document_meta(text: str) -> bool:
    return bool(META_RE.match(text))


def parse_neorg_labels(api: KeepApi, labels_str: str) -> t.List[Label]:
    """Parse Gkeep labels from neorg categories meta field

    The reason we are not simply using labels_str.split(' ') is that Gkeep labels could
    have a space in them. Instead, this algorithm will attempt to find a label that
    matches the start of the string and then increment the pointer.
    """
    i = 0
    labels = []
    while i < len(labels_str):
        try:
            j = labels_str.index(" ", i)
        except ValueError:
            j = len(labels_str)
        word = labels_str[i:j].lower()
        candidates = []
        # Find all labels that start with the current word
        for label in api.labels():
            name = label.name.lower()
            if name.startswith(word):
                candidates.append(label)
        # Check the longest labels first
        candidates.sort(key=lambda l: len(l.name), reverse=True)
        rem = labels_str[i:].lower()
        for label in candidates:
            # If the line at our current position matches the label title, use that
            # label and move the cursor forward by the length of the label
            if rem.startswith(label.name.lower()):
                labels.append(label)
                i += len(label.name) + 1
                break
        else:
            # No label found that exactly matches. Move cursor to next word
            i = j + 1
    return labels


def parse(api: KeepApi, lines: t.Sequence[str], note: Note) -> None:
    text = "\n".join(lines)
    if text != note.text:
        note.text = text

    meta = parse_meta(lines)
    if meta is None:
        return
    if "title" in meta and meta["title"] != note.title:
        note.title = meta["title"]
    if "categories" in meta:
        labels = parse_neorg_labels(api, meta["categories"])
        merge_labels(note, labels)


def parse_meta(file: TFile) -> t.Optional[t.Dict[str, str]]:
    lines = read_lines(file, 20)
    in_meta = False
    meta: t.Dict[str, str] = {}
    for line in lines:
        if META_LINE_RE.match(line):
            in_meta = True
        elif in_meta:
            if END_RE.match(line):
                return meta
            key, val = [l.strip() for l in line.split(":", 1)]
            meta[key] = val
    return None


def write_meta(
    lines: t.Iterable[str], meta: t.Optional[t.Dict[str, str]]
) -> t.Iterable[str]:
    in_meta = False
    seen = set()
    for line in lines:
        if META_LINE_RE.match(line):
            in_meta = True
            if meta is None:
                continue
        elif in_meta:
            if END_RE.match(line):
                in_meta = False
                if meta is None:
                    continue
                for k, v in meta.items():
                    if k not in seen:
                        yield f"\t{k}: {v}"
            elif meta is None:
                continue
            else:
                key, val = [l.strip() for l in line.split(":", 1)]
                seen.add(key)
                if key in meta and val != meta[key]:
                    yield f"\t{key}: {meta[key]}"
                    continue
        yield line


def create_meta(note: Note) -> t.List[str]:
    categories = " ".join([l.name for l in note.labels.all()])
    return [
        "@document.meta",
        f"\ttitle: {note.title}",
        "\tdescription:",
        f"\tauthor: {getpass.getuser()}",
        f"\tcategories: {categories}",
        f"\tcreated: {note.timestamps.created.date().isoformat()}",
        "\tversion: 0.1",
        f"\tgkeep: {note.id}",
        "@end",
        "",
    ]


def get_metadata(file: TFile) -> Header:
    meta = parse_meta(file) or {}
    return Header(meta.get("gkeep"), meta.get("title"))


def serialize(note: Note) -> t.Iterator[str]:
    lines = note.text.split("\n")
    meta = parse_meta(lines)
    if meta is not None:
        meta["gkeep"] = note.id
        # TODO add labels to categories
        yield from write_meta(lines, meta)
    else:
        yield from create_meta(note)
        yield from lines


def write_file_meta(
    filename: str, id: str, title: str, file: t.Optional[TFile] = None
) -> None:
    if file is None:
        file = filename
    lines = read_lines(file)
    meta = parse_meta(lines)
    if meta is None or "gkeep" in meta:
        return
    meta["gkeep"] = id
    meta["title"] = title
    with open(filename, "w") as ofile:
        for line in write_meta(lines, meta):
            ofile.write(line)
            ofile.write("\n")


def write_buffer_meta(buffer: Buffer, id: str, title: str) -> None:
    lines = buffer[:]
    meta = parse_meta(lines)
    if meta is None or "gkeep" in meta:
        return
    meta["gkeep"] = id
    meta["title"] = title
    buffer[:] = list(write_meta(lines, meta))


def convert_to_neorg(note: Note) -> None:
    note.text = "\n".join(serialize(note))


def convert_from_neorg(note: Note) -> None:
    lines = note.text.split("\n")
    note.text = "\n".join(write_meta(lines, None))
