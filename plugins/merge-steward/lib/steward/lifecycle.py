"""Steward start-up, the queue watcher, and status reporting."""
from __future__ import annotations

import sys
import time
from typing import List, Optional, Set, TextIO, Tuple

from . import decide, events, gitutil, lock, store, worktree
from .config import Config
from .paths import Repo


def init(repo: Repo, cfg: Config, session_name: str) -> List[str]:
    messages: List[str] = []
    repo.ensure_dirs()
    prev = lock.acquire(repo, session_name)
    if prev and prev.get("sessionName") != session_name:
        messages.append(f"took over a stale lock from {prev.get('sessionName')}")
    if gitutil.rev(f"refs/heads/{cfg.integration}", repo.main_checkout) is None:
        gitutil.git(["branch", cfg.integration, cfg.main], repo.main_checkout)
        messages.append(f"created {cfg.integration} from {cfg.main}")
    worktree.ensure(repo, cfg)
    for req in store.list_requests(repo, "processing"):
        merged = decide.merge_commit_for(repo, cfg, req)
        if merged:
            decide.complete_merge(repo, cfg, req["id"], merged)
            messages.append(f"finished the merge of {req['id']}, which was already on {cfg.integration}")
            continue
        store.move(repo, req["id"], "processing", "queue", lastVerify=None, requeued=True)
        messages.append(f"returned {req['id']} to the queue")
    if not worktree.sync_main(repo, cfg, cfg.main):
        events.record(repo, "human-requested", reason="main-merge-conflict")
        messages.append(f"WARNING: merging {cfg.main} into {cfg.integration} conflicts; a human must resolve it")
    messages.append(f"steward ready: {session_name} on {cfg.integration}")
    return messages


def watch(repo: Repo, interval: float, once: bool = False, out: Optional[TextIO] = None,
          err: Optional[TextIO] = None) -> None:
    """Print one line per request that appears in the queue. Meant to run under the Monitor tool.

    This loop is the steward's only heartbeat, so a failing pass is reported on `err` and the loop carries on.
    Ctrl-C ends it cleanly.
    """
    out = out or sys.stdout
    err = err or sys.stderr
    seen: Set[Tuple[str, bool]] = set()
    try:
        while True:
            try:
                _watch_pass(repo, seen, out)
            except Exception as e:  # noqa: BLE001 - keep the heartbeat alive whatever one pass hit
                print(f"steward watch: {type(e).__name__}: {e} (continuing)", file=err, flush=True)
            if once:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        return


def _watch_pass(repo: Repo, seen: Set[Tuple[str, bool]], out: TextIO) -> None:
    lock.heartbeat(repo)
    for req in store.list_requests(repo, "queue"):
        key = (req["id"], bool(req.get("approved")))
        if key in seen:
            continue
        seen.add(key)
        tag = "APPROVED" if req.get("approved") else "NEW"
        print(f"{tag} {req['id']} {req.get('branch')}", file=out, flush=True)


def status(repo: Repo, cfg: Config) -> dict:
    state = store.read_state(repo)
    recent = sorted(
        [dict(r, state="done") for r in store.list_requests(repo, "done")]
        + [dict(r, state="rejected") for r in store.list_requests(repo, "rejected")],
        key=lambda r: r.get("mergedAt") or r.get("rejectedAt") or r["submittedAt"],
    )[-10:]
    return {
        "steward": lock.read(repo),
        "queue": store.list_requests(repo, "queue"),
        "processing": store.list_requests(repo, "processing"),
        "awaiting": store.list_requests(repo, "awaiting"),
        "recent": recent,
        "mergesSincePromote": state["mergesSincePromote"],
        "everyNMerges": cfg.every_n_merges,
    }


def format_status(s: dict) -> str:
    def line(r: dict) -> str:
        return f"  {r['id']}  {r['branch']}  {r.get('summary', '').splitlines()[0] if r.get('summary') else ''}"

    parts = [f"steward: {s['steward'].get('sessionName') if s['steward'] else '(none)'}"]
    for key in ("processing", "queue", "awaiting"):
        parts.append(f"{key} ({len(s[key])}):")
        for r in s[key]:
            parts.append(line(r))
            if key == "awaiting":
                risk = r["risk"]
                parts.append(f"    risk: paths={risk['paths']} deletions={risk['deletions']} notes={risk['notes']}")
    parts.append("recent:")
    for r in s["recent"]:
        parts.append(f"  [{r['state']}{('/' + r['rejectKind']) if r.get('rejectKind') else ''}] {r['id']}  {r['branch']}")
    parts.append(f"merges since last promote: {s['mergesSincePromote']}/{s['everyNMerges']}")
    return "\n".join(parts)
