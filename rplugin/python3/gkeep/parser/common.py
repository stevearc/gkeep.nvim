import typing as t

from gkeepapi.node import Label, TopLevelNode
from pynvim.api import Buffer


def merge_labels(note: TopLevelNode, labels: t.Sequence[Label]) -> None:
    for label in labels:
        if note.labels.get(label.id) is None:
            note.labels.add(label)
    for label in note.labels.all():
        if label not in labels:
            note.labels.remove(label)


TFile = t.Union[str, t.Sequence[str], Buffer]


def read_lines(file: TFile, count: t.Optional[int] = None) -> t.Sequence[str]:
    if isinstance(file, str):
        ret = []
        with open(file, "r") as ifile:
            for line in ifile:
                # Trim off the trailing newline
                ret.append(line[:-1])
                if count is not None and len(ret) >= count:
                    break
        return ret
    elif isinstance(file, Buffer):
        return file[0:count]
    else:
        return file[0:count]


class Header(t.NamedTuple):
    id: t.Optional[str]
    title: t.Optional[str]
