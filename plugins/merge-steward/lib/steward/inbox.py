"""Rejection notices addressed to a worker branch."""
from __future__ import annotations

import os
from typing import Optional

from .paths import Repo, safe_name
from .store import now_iso, read_json, write_json_atomic


def _path(repo: Repo, branch: str) -> str:
    return repo.path("inbox", f"{safe_name(branch)}.json")


def write_notice(repo: Repo, req: dict, kind: str, reason: str, evidence: str) -> dict:
    notice = {
        "requestId": req["id"],
        "branch": req["branch"],
        "kind": kind,
        "reason": reason,
        "evidence": evidence,
        "createdAt": now_iso(),
    }
    write_json_atomic(_path(repo, req["branch"]), notice)
    return notice


def pending(repo: Repo, branch: str) -> Optional[dict]:
    try:
        return read_json(_path(repo, branch))
    except FileNotFoundError:  # none, or acknowledged by another process just now
        return None


def ack(repo: Repo, branch: str) -> Optional[dict]:
    notice = pending(repo, branch)
    if notice is None:
        return None
    repo.ensure_dirs()
    try:
        os.replace(_path(repo, branch), repo.path("inbox", "acked", f"{safe_name(branch)}-{notice['requestId']}.json"))
    except FileNotFoundError:  # acknowledged concurrently (e.g. submit's auto-ack vs `steward inbox --ack`)
        return None
    return notice


def format_notice(notice: dict) -> str:
    if notice["kind"] == "denied":
        return (
            f"The user denied merge request {notice['requestId']}, which the merge steward held for approval.\n\n"
            f"{notice['reason']}\n\n"
            "Do not resubmit this change as it is. Talk to the user about what they want instead, "
            "then run `steward inbox --ack`."
        )
    return (
        f"The merge steward rejected merge request {notice['requestId']} ({notice['kind']}).\n\n"
        f"{notice['reason']}\n\n"
        f"--- evidence ---\n{notice['evidence']}\n--- end evidence ---\n\n"
        "Fix this on your branch and run the tests, then run `steward inbox --ack` and submit again."
    )
