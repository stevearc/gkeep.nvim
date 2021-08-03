import logging
import os

import gkeep.api
import gkeep.config
import gkeep.parser as parser
from gkeep.util import NoteUrl, get_type
from pynvim.api import Buffer, Nvim

logger = logging.getLogger(__name__)


class NoteView:
    def __init__(self, vim: Nvim, config: gkeep.config.Config, api: gkeep.api.KeepApi):
        self._vim = vim
        self._config = config
        self._api = api

    def render(self, bufnr: Buffer, url: NoteUrl) -> None:
        note = self._api.get(url.id)
        if note is None:
            bufnr[:] = []
            return
        url.title = note.title
        ext = os.path.splitext(bufnr.name)[1]
        bufnr.options["filetype"] = self._config.ft_from_ext(ext)
        bufnr[:] = list(parser.serialize(self._config, note))
        bufnr.options["modified"] = False
        nt = get_type(note)
        bufnr.vars["note_type"] = nt.value

    def rerender_note(self, id: str) -> None:
        for bufnr in self._vim.buffers:
            url = parser.url_from_file(self._config, bufnr.name, bufnr)
            if url is not None and url.id == id:
                self.render(bufnr, url)
                break

    def save_buffer(self, bufnr: Buffer) -> None:
        url = parser.url_from_file(self._config, bufnr.name, bufnr)
        if url is None:
            return
        note = self._api.get(url.id)
        if note is None:
            util.echoerr(self._vim, f"Note {url.id} not found")
            return

        parser.parse(self._api, self._config, bufnr, note)
        url.title = note.title
        self.render(bufnr, url)
        bufnr.name = url.bufname(self._api, self._config, note)
