import sys
import time
import typing as t
from functools import wraps

if sys.version_info < (3, 8):
    from typing_extensions import Literal
else:
    from typing import Literal

_status_stack = []

F = t.TypeVar("F", bound=t.Callable[..., t.Any])


class Status:
    def __init__(self, msg: str):
        self.msg = msg
        self._active = False

    def start(self) -> "Status":
        if not self._active:
            _status_stack.append(self.msg)
            self._active = True
        return self

    def stop(self) -> None:
        if self._active:
            self._active = False
            _status_stack.remove(self.msg)

    def __enter__(self) -> None:
        _status_stack.append(self.msg)

    def __exit__(self, *_: t.Any) -> None:
        _status_stack.pop()

    def __call__(self, f: F) -> F:
        @wraps(f)
        def d(*args: t.Any, **kwargs: t.Any) -> t.Any:
            _status_stack.append(self.msg)
            try:
                return f(*args, **kwargs)
            finally:
                _status_stack.pop()

        return d  # type: ignore[return-value]


status = Status


class Spinner:
    def __init__(self) -> None:
        self.fps = 14
        self._start_time: t.Optional[float] = None
        self._frames = DEFAULT_FRAMES

    def reset(self) -> None:
        self._start_time = None

    @property
    def frame(self) -> str:
        if self._start_time is None:
            self._start_time = time.time()
        idx = round(self.fps * (time.time() - self._start_time))
        idx = idx % len(self._frames)
        return self._frames[idx]


def get_status(
    include_spinner: t.Union[bool, Literal["right"]] = False
) -> t.Optional[str]:
    if _status_stack:
        st = _status_stack[-1]
        if include_spinner == "right":
            st = st + " " + default_spinner.frame
        elif include_spinner:
            st = default_spinner.frame + " " + st
        return st
    else:
        default_spinner.reset()
        return None


# Dots spinner is from https://github.com/sindresorhus/cli-spinners
# MIT License

# Copyright (c) Sindre Sorhus <sindresorhus@gmail.com> (https://sindresorhus.com)

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
# fmt: off
DEFAULT_FRAMES = [
    "⢀⠀", "⡀⠀", "⠄⠀", "⢂⠀", "⡂⠀", "⠅⠀", "⢃⠀", "⡃⠀", "⠍⠀", "⢋⠀", "⡋⠀", "⠍⠁", "⢋⠁", "⡋⠁",
    "⠍⠉", "⠋⠉", "⠋⠉", "⠉⠙", "⠉⠙", "⠉⠩", "⠈⢙", "⠈⡙", "⢈⠩", "⡀⢙", "⠄⡙", "⢂⠩", "⡂⢘", "⠅⡘",
    "⢃⠨", "⡃⢐", "⠍⡐", "⢋⠠", "⡋⢀", "⠍⡁", "⢋⠁", "⡋⠁", "⠍⠉", "⠋⠉", "⠋⠉", "⠉⠙", "⠉⠙", "⠉⠩",
    "⠈⢙", "⠈⡙", "⠈⠩", "⠀⢙", "⠀⡙", "⠀⠩", "⠀⢘", "⠀⡘", "⠀⠨", "⠀⢐", "⠀⡐", "⠀⠠", "⠀⢀", "⠀⡀",
]

default_spinner = Spinner()
