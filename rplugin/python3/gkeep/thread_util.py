import logging
import threading
import typing as t
from functools import partial, wraps

logger = logging.getLogger(__name__)

F = t.TypeVar("F", bound=t.Callable[..., t.Any])


def wrap_lock(lock: threading.Lock, func: F, max_waiting: int = None) -> F:
    if max_waiting is None:

        @wraps(func)
        def w(*args, **kwargs):  # type: ignore
            with lock:
                func(*args, **kwargs)

    else:
        wait_count: t.Dict[t.Any, int] = {}
        run_count: t.Dict[t.Any, int] = {}
        countlock = threading.Lock()

        @wraps(func)
        def w(*args, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs:
                raise Exception(
                    "Cannot use keyword arguments for @background function with max_waiting"
                )
            with countlock:
                num_running = run_count.setdefault(args, 0)
                num_waiting = wait_count.setdefault(args, 0)
                if max_waiting == 0:
                    if num_running > 0 or num_waiting > 0:
                        return
                elif num_waiting >= max_waiting:
                    return
                wait_count[args] += 1
            with lock:
                with countlock:
                    run_count[args] += 1
                    wait_count[args] -= 1
                try:
                    func(*args, **kwargs)
                finally:
                    with countlock:
                        run_count[args] -= 1

    return w  # type: ignore[return-value]


def wrap_log_exception(func: F) -> F:
    @wraps(func)
    def w(*args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            func(*args, **kwargs)
        except Exception:
            logger.exception("Exception while running background thread")
            raise

    return w  # type: ignore[return-value]


def background(*args: t.Any, **kwargs: t.Any) -> t.Any:
    if args:
        func = args[0]
    else:
        return partial(background, **kwargs)
    use_lock = kwargs.pop("lock", True)
    max_waiting = kwargs.pop("max_waiting", None)
    target = wrap_log_exception(func)
    if use_lock:
        lock = threading.Lock()
        target = wrap_lock(lock, target, max_waiting)

    @wraps(func)
    def w(*args: t.Any, **kwargs: t.Any) -> None:
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()

    return w
