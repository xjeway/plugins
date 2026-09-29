"""Request files, their state directories, and the small steward state file."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from .errors import StewardError
from .paths import STATES, Repo, safe_name

ACTIVE_STATES = ("queue", "processing", "awaiting", "done")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json_atomic(path: str, data: dict) -> None:
    d, name = os.path.split(path)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".{name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def req_path(repo: Repo, state: str, rid: str) -> str:
    return repo.path(state, f"{rid}.json")


def new_id(repo: Repo, branch: str) -> str:
    base = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + safe_name(branch)
    rid, n = base, 1
    while any(os.path.exists(req_path(repo, s, rid)) for s in STATES):
        n += 1
        rid = f"{base}-{n}"
    return rid


def list_requests(repo: Repo, state: str) -> List[dict]:
    d = repo.path(state)
    if not os.path.isdir(d):
        return []
    reqs = []
    for n in os.listdir(d):
        if not n.endswith(".json") or n.startswith("."):
            continue
        req = _read_request(os.path.join(d, n))
        if req is not None:
            reqs.append(req)
    return sorted(reqs, key=lambda r: (r.get("submittedAt", ""), r["id"]))


def _read_request(path: str) -> Optional[dict]:
    """A request file, or None if it vanished (moved by another process) or is not a readable request."""
    try:
        req = read_json(path)
    except (FileNotFoundError, ValueError):
        return None
    return req if isinstance(req, dict) and "id" in req else None


def save(repo: Repo, state: str, req: dict) -> None:
    write_json_atomic(req_path(repo, state, req["id"]), req)


def locate(repo: Repo, ref: str, states: Sequence[str] = STATES) -> Optional[Tuple[str, dict]]:
    """Find a request by id, or the newest request for a branch name."""
    for s in states:
        req = _read_request(req_path(repo, s, ref))
        if req is not None:
            return s, req
    found = [(s, r) for s in states for r in list_requests(repo, s) if r.get("branch") == ref]
    if not found:
        return None
    return max(found, key=lambda sr: (sr[1].get("submittedAt", ""), sr[1]["id"]))


def move(repo: Repo, rid: str, src: str, dst: str, **updates) -> dict:
    """Move a request between states. Updates are written to the src file first (atomically), then it is renamed."""
    src_p = req_path(repo, src, rid)
    try:
        req = read_json(src_p)
    except FileNotFoundError:
        raise StewardError(f"request {rid} is not in {src}") from None
    if updates:
        req.update(updates)
        write_json_atomic(src_p, req)
    os.replace(src_p, req_path(repo, dst, rid))
    return req


def head_known(repo: Repo, head: str) -> bool:
    return any(r.get("head") == head for s in ACTIVE_STATES for r in list_requests(repo, s))


def read_state(repo: Repo) -> dict:
    p = repo.path("state.json")
    state = {"mergesSincePromote": 0, "lastPromoteAt": None}
    if os.path.exists(p):
        state.update(read_json(p))
    return state


def write_state(repo: Repo, state: dict) -> None:
    write_json_atomic(repo.path("state.json"), state)
