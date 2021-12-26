import logging

import gkeep.api
import gkeep.config
from gkeep import parser, util
from gkeep.util import NoteUrl
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
        ext = util.get_ext(bufnr.name)
        ft = self._config.ft_from_ext(ext)
        if ft != bufnr.options["filetype"]:
            bufnr.options["filetype"] = ft
        bufnr[:] = list(parser.serialize(self._config, note))
        bufnr.options["modified"] = False
        util.set_note_opts_and_vars(note, bufnr)

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
