"""One steward per repository. `steward watch` refreshes the heartbeat; a silent lock goes stale."""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Optional

from .errors import StewardError
from .paths import Repo
from .store import now_iso, read_json, write_json_atomic

STALE_AFTER_SEC = 120


class LockHeld(StewardError):
    pass


def _path(repo: Repo) -> str:
    return repo.path("steward.lock")


def read(repo: Repo) -> Optional[dict]:
    """The lock, None if absent. An unreadable/partial lock file comes back as {} (which is_stale treats as stale)."""
    try:
        data = read_json(_path(repo))
    except FileNotFoundError:
        return None
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def is_stale(lock: dict, now: Optional[float] = None) -> bool:
    try:
        beat = datetime.fromisoformat(lock["heartbeatAt"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return True
    return (now if now is not None else time.time()) - beat > STALE_AFTER_SEC


def _create_exclusive(repo: Repo, session_name: str) -> bool:
    """Atomically create the lock with full content (write temp, hard-link into place). False if it already exists."""
    path = _path(repo)
    ts = now_iso()
    tmp = os.path.join(os.path.dirname(path), f".steward.lock.{os.getpid()}.{os.urandom(4).hex()}.new")
    write_json_atomic(tmp, {"sessionName": session_name, "startedAt": ts, "heartbeatAt": ts})
    try:
        os.link(tmp, path)
        return True
    except FileExistsError:
        return False
    finally:
        os.unlink(tmp)


def acquire(repo: Repo, session_name: str) -> Optional[dict]:
    """Take the lock. Returns the previous lock (if any). Raises LockHeld if another live steward has it."""
    prev: Optional[dict] = None
    for _ in range(5):
        if _create_exclusive(repo, session_name):
            lock = read(repo)
            if lock and lock.get("sessionName") == session_name:
                return prev or None
            raise LockHeld("lost the race for the steward lock")
        cur = read(repo)
        if cur is None:
            continue  # vanished between create and read; retry
        if cur and cur.get("sessionName") != session_name and not is_stale(cur):
            raise LockHeld(f"another steward is active: {cur.get('sessionName')} (last heartbeat {cur.get('heartbeatAt')})")
        prev = cur
        if cur.get("sessionName") == session_name:
            ts = now_iso()
            write_json_atomic(_path(repo), {"sessionName": session_name, "startedAt": ts, "heartbeatAt": ts})
            return prev
        # stale or unreadable: remove it (only if still stale) and race for a fresh exclusive create
        again = read(repo)
        if again is not None and is_stale(again):
            try:
                os.unlink(_path(repo))
            except FileNotFoundError:
                pass
    raise LockHeld("could not acquire the steward lock")


def heartbeat(repo: Repo, session_name: Optional[str] = None) -> None:
    lock = read(repo)
    if not lock or "heartbeatAt" not in lock:
        return
    if session_name is not None and lock.get("sessionName") != session_name:
        raise LockHeld(f"lock is owned by {lock.get('sessionName')}")
    lock["heartbeatAt"] = now_iso()
    write_json_atomic(_path(repo), lock)
