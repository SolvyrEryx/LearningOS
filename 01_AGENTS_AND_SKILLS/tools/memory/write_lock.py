"""Serialize study/exam writers. OS locks release on process exit; no stale PID repair."""
from contextlib import contextmanager
from functools import wraps
import os
import threading

from common import LearningOSUsageError

_thread = threading.local()
_mutex = threading.RLock()


@contextmanager
def writer(ws):
    with _mutex:
        key = str(ws.root)
        depths = getattr(_thread, "depths", {})
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        path = ws.memory_global / ".learning-writer.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0"); handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise LearningOSUsageError("Another LearningOS operation is saving this workspace. Wait for its receipt, then retry with the same operation id.")
            depths[key] = 1
            _thread.depths = depths
            try:
                yield
            finally:
                depths[key] = 0
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def serialized(function):
    @wraps(function)
    def wrapped(ws, *args, **kwargs):
        with writer(ws):
            return function(ws, *args, **kwargs)
    return wrapped
