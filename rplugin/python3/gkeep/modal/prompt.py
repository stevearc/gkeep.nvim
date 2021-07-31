import typing as t

from gkeep.modal.layout import open_win
from pynvim.api import Buffer, Nvim, Window


class Prompt:
    def __init__(self, vim: Nvim):
        self._vim = vim
        self._window: t.Optional[Window] = None
        self._callback: t.Optional[t.Callable[[str], None]] = None
        self._on_change: t.Optional[t.Callable[[str], None]] = None
        self._on_cancel: t.Optional[t.Callable[[], None]] = None

    def _create_buffer(self) -> Buffer:
        bufnr = self._vim.api.create_buf(False, True)
        bufnr.options["buftype"] = "prompt"
        bufnr.options["bufhidden"] = "wipe"
        bufnr.options["swapfile"] = False
        if self._on_change is not None:
            self._vim.command(
                "autocmd TextChangedI <buffer=%d> call _gkeep_modal('prompt', 'changed')"
                % bufnr.number
            )
        return bufnr

    def show(
        self,
        callback: t.Callable[[str], None],
        on_cancel: t.Callable[[], None] = None,
        on_change: t.Callable[[str], None] = None,
        prompt: str = "➤ ",
        text: str = None,
        secret: bool = False,
        **kwargs: t.Any,
    ) -> None:
        if self._window is not None and self._window.valid:
            return
        self._callback = callback
        self._on_change = on_change
        self._on_cancel = on_cancel
        bufnr = self._create_buffer()
        self._window = open_win(self._vim, bufnr, height=1, **kwargs)
        if secret:
            self._vim.command("syntax match line '.' conceal cchar=*")
            self._vim.command(f"syntax match prefix '^{prompt}'")
            self._window.options["concealcursor"] = "nvic"
            self._window.options["conceallevel"] = 2
        self._vim.funcs.prompt_setcallback(bufnr, "_gkeep_prompt_close")
        self._vim.funcs.prompt_setinterrupt(bufnr, "_gkeep_prompt_close")
        self._vim.funcs.prompt_setprompt(bufnr, prompt)
        self._vim.command("startinsert!")
        if text is not None:
            self._vim.funcs.feedkeys(text)

    def close(self, text: str = None) -> None:
        if self._window is not None and self._window.valid:
            self._window.api.close(True)
        self._window = None
        callback = self._callback
        on_cancel = self._on_cancel
        self._callback = None
        self._on_change = None
        self._on_cancel = None
        if text is not None and callback is not None:
            callback(text)
        elif text is None and on_cancel is not None:
            on_cancel()

    def changed(self) -> None:
        if self._window is None or not self._window.valid or self._on_change is None:
            return
        bufnr = self._window.buffer
        text = bufnr[0]
        prefix = self._vim.funcs.prompt_getprompt(bufnr)
        text = text[len(prefix) :]
        self._on_change(text)
