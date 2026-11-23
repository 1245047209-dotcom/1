"""Serialize read-modify-write operations on state shared by app processes."""
from contextlib import contextmanager
import os
from pathlib import Path
import threading

_THREAD_LOCK = threading.RLock()


@contextmanager
def state_file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the lock file in place so a waiting process always locks the same
    # file, even while the JSON itself is atomically replaced.
    with _THREAD_LOCK, path.with_suffix(".lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
