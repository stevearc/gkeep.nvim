import typing as t
from abc import ABC, abstractmethod

import gkeep.api
import gkeep.config
from gkeep import util
from gkeep.modal import Element, GridLayout, Modal
from pynvim.api import Buffer, Nvim, Window


class View(ABC):
    def __init__(
        self,
        vim: Nvim,
        config: gkeep.config.Config,
        api: gkeep.api.KeepApi,
        modal: Modal,
        title: str,
    ):
        self._vim = vim
        self._config = config
        self._api = api
        self._modal = modal
        self._bufnr: t.Optional[Buffer] = None
        self._title = title

    @abstractmethod
    def _get_shortcuts(self) -> t.List[t.Tuple[str, str, str, str]]:
        raise NotImplementedError

    @property
    def bufnr(self) -> t.Optional[Buffer]:
        if self._bufnr is not None and self._bufnr.valid:
            return self._bufnr
        return None

    @property
    def is_visible(self) -> bool:
        return self.get_win() is not None

    def get_win(self) -> t.Optional[Window]:
        bufnr = self.bufnr
        if bufnr is None:
            return None
        curwin = self._vim.current.window
        if curwin.buffer == bufnr:
            return curwin
        for window in self._vim.current.tabpage.windows:
            if bufnr == window.buffer:
                return window
        return None

    def _create_buffer(self) -> None:
        self._bufnr = self._vim.api.create_buf(False, True)
        assert self._bufnr is not None
        self._vim.current.buffer = self._bufnr
        self._bufnr.options["buftype"] = "nofile"
        self._bufnr.options["bufhidden"] = "wipe"
        self._bufnr.options["swapfile"] = False
        self._bufnr.options["modifiable"] = False

        for modes, lhs, rhs, _ in self._get_shortcuts():
            self.keymap(lhs, rhs, modes)
        self._setup_buffer(self._bufnr)

    @abstractmethod
    def _setup_buffer(self, buffer: Buffer) -> None:
        raise NotImplementedError

    def _configure_win(self, window: Window) -> None:
        window.options["winfixwidth"] = True
        window.options["number"] = False
        window.options["relativenumber"] = False
        window.options["signcolumn"] = "no"
        window.options["foldcolumn"] = "0"
        window.options["wrap"] = False
        window.api.set_width(self._config.width)
        self._setup_win(window)

    def _setup_win(self, window: Window) -> None:
        pass

    @property
    def is_inside(self) -> bool:
        return self._vim.current.buffer == self.bufnr

    def close(self) -> None:
        window = self.get_win()
        if window is not None:
            window.api.close(True)

    def is_normal_win(self, window: Window) -> bool:
        config = window.api.get_config()
        if config["relative"] != "":
            return False
        if self._vim.funcs.win_gettype(window) != "":
            return False
        if window.options["previewwindow"]:
            return False
        if window.buffer.options["buftype"] != "":
            return False
        return True

    def keymap(
        self,
        lhs: str,
        rhs: str,
        modes: str = "n",
        opts: t.Optional[t.Dict[str, bool]] = None,
    ) -> None:
        if self._bufnr:
            if opts is None:
                opts = {"silent": True, "noremap": True}
            for mode in modes:
                # Make sure we leave visual mode after executing the map
                if mode == "v":
                    rhs += "<Esc>"
                self._bufnr.api.set_keymap(mode, lhs, rhs, opts)

    def action(self, action: str, *args: t.Any) -> None:
        meth = f"cmd_{action}"
        if hasattr(self, meth):
            getattr(self, meth)(*args)
        else:
            util.echoerr(self._vim, f"Unknown Gkeep action '{action}'")

    def cmd_show_help(self) -> None:
        elements = []
        for _, lhs, _, desc in self._get_shortcuts():
            elements.append(Element(lhs, [(lhs.ljust(6), "Special"), (desc, "Normal")]))
        layout = GridLayout(self._vim, GridLayout.cols_from_1d(elements, 0))
        self._modal.confirm.show(
            f"{self._title} shortcuts",
            lambda _: None,
            layout=layout,
            cancel_keys=["?"],
            text_margin=1,
        )
