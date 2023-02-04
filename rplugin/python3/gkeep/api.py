import logging
import typing as t
from collections import Counter
from functools import cmp_to_key

from gkeep.query import Query
from gkeep.status import status
from gkeep.thread_util import background
from gkeep.util import NoteType, escape
from gkeepapi import Keep, exception
from gkeepapi.node import Label, TopLevelNode

logger = logging.getLogger(__name__)


# Monkey patch the title property because we can't support newlines
def _get_title(self: TopLevelNode) -> str:
    return self._title.strip().replace("\n", " ") if self._title else self._title


def _set_title(self: TopLevelNode, title: str) -> None:
    self._title = title
    self.touch(True)


TopLevelNode.title = property(_get_title, _set_title)


def cmp(a: NoteType, b: NoteType) -> int:
    if a.pinned != b.pinned:
        return -1 if a.pinned else 1
    return int(b.sort) - int(a.sort)


class KeepApi(Keep):
    _search_query: t.Optional[Query] = None
    _search_results: t.Optional[t.List[NoteType]] = None
    _title_count: t.Counter[str] = Counter()
    _archived_title_count: t.Counter[str] = Counter()

    @property
    def is_logged_in(self) -> bool:
        return (
            self._keep_api.getAuth() is not None and self.getMasterToken() is not None
        )

    @property
    def is_dirty(self) -> bool:
        labels_updated = any((i.dirty for i in self._labels.values()))
        return labels_updated or bool(self._findDirtyNodes())

    def find_unique_label(self, name: str) -> t.Optional[Label]:
        found = None
        for l in self._labels.values():
            lname = l.name.lower()
            if name == lname:
                found = l
                break
            elif lname.startswith(name):
                if found is None:
                    found = l
                else:
                    # More than one label starts with this string. It's ambiguous, so we have to bail out
                    return None
        return found

    def get_email(self) -> t.Optional[str]:
        auth = self._keep_api.getAuth()
        if auth is not None:
            return auth.getEmail()
        return None

    def has_unique_title(self, note: TopLevelNode) -> bool:
        counter = self._archived_title_count if note.archived else self._title_count
        return counter.get(escape(note.title), 0) < 2

    def sync(
        self,
        callback: t.Callable[..., None],
        error_callback: t.Callable[[str], None],
        resync: bool = False,
    ) -> None:
        if resync:
            keep_version = None
            changed_nodes = []
            changed_labels = None
        else:
            keep_version = self._keep_version
            changed_nodes = [i.save() for i in self._findDirtyNodes()]
            changed_labels = None
            if any((i.dirty for i in self._labels.values())):
                changed_labels = [i.save() for i in self._labels.values()]

        if changed_nodes:
            logger.debug("Sending %d changed nodes to server", len(changed_nodes))
        if changed_labels:
            logger.debug("Sending %d changed labels to server", len(changed_labels))
        self._sync_notes(
            resync,
            keep_version,
            changed_nodes,
            changed_labels,
            callback,
            error_callback,
        )

    def apply_updates(
        self,
        resync: bool,
        keep_version: str,
        user_info: t.Any,
        nodes: t.Sequence[t.Any],
    ) -> t.Set[str]:
        if resync:
            self._clear()
        self._title_count.clear()
        self._archived_title_count.clear()
        note_timestamps = {}
        for note in self.all():
            note_timestamps[note.id] = note.timestamps.updated
        updated_notes = set()

        self._keep_version = keep_version
        for info in user_info:
            self._parseUserInfo(info)
        for node in nodes:
            self._parseNodes(node)

        for note in self.all():
            if not note.trashed:
                if note.archived:
                    self._archived_title_count.update([escape(note.title)])
                else:
                    self._title_count.update([escape(note.title)])
            if note_timestamps.get(note.id) != note.timestamps.updated:
                updated_notes.add(note.id)
        return updated_notes

    @background
    def _sync_notes(
        self,
        resync: bool,
        keep_version: t.Optional[str],
        changed_nodes: t.List[t.Any],
        changed_labels: t.Optional[t.List[t.Any]],
        callback: t.Callable[..., None],
        error_callback: t.Callable[[str], None],
    ) -> None:
        try:
            user_info: t.List[t.Any] = []
            nodes: t.List[t.Any] = []
            # Fetch updates until we reach the newest version.
            msg = "Fetching notes" if resync else "Syncing changes"
            with status(msg):
                while True:
                    logger.debug("Starting keep sync: %s", keep_version)

                    # Collect any changes and send them up to the server.
                    changes = self._keep_api.changes(
                        target_version=keep_version,
                        nodes=changed_nodes,
                        labels=changed_labels,
                    )
                    changed_nodes = []
                    changed_labels = None

                    if changes.get("forceFullResync"):
                        raise exception.ResyncRequiredException("Full resync required")

                    if changes.get("upgradeRecommended"):
                        raise exception.UpgradeRecommendedException(
                            "Upgrade recommended"
                        )

                    if "userInfo" in changes:
                        user_info.append(changes["userInfo"])

                    if "nodes" in changes:
                        nodes.append(changes["nodes"])

                    keep_version = changes["toVersion"]
                    logger.debug("Finishing sync: %s", keep_version)

                    # Check if there are more changes to retrieve.
                    if not changes["truncated"]:
                        break

            callback(resync, keep_version, user_info, nodes)
        except Exception as e:
            error_callback(str(e))
            raise

    @background(lock=False)
    @status("Searching")
    def run_search(
        self, query: Query, callback: t.Callable[[], None], force: bool = False
    ) -> None:
        if query == self._search_query and not force:
            callback()
            return
        self._search_query = query
        self._search_results = None
        results = []
        for n in self.all():
            if query != self._search_query:
                return
            if query.match(self, n):
                results.append(n)
        results.sort(key=cmp_to_key(cmp))
        self._search_results = results
        callback()

    def _clear(self) -> None:
        super()._clear()
        self._title_count.clear()

    def resort(self, query: Query) -> None:
        if self._search_query == query and self._search_results:
            self._search_results.sort(key=cmp_to_key(cmp))

    def get_search(self, query: Query) -> t.List[NoteType]:
        if self._search_query == query:
            return self._search_results or []
        else:
            return []

    def add_search_result(self, query: Query, note: NoteType) -> bool:
        if self._search_query == query and self._search_results:
            self._search_results.append(note)
            return True
        return False

    def is_searching(self, query: Query) -> bool:
        return query == self._search_query and self._search_results is None

    def logout(self) -> None:
        auth = self._keep_api.getAuth()
        if auth is not None:
            auth.logout()
        self._search_query = None
        self._search_results = None
        self._clear()
