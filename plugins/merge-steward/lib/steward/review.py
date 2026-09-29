"""Claim the next request and gather what the steward needs to review it."""
from __future__ import annotations

from typing import List, Optional, Tuple

from . import events, gitutil, store
from .config import Config
from .errors import StewardError
from .paths import Repo


def processing(repo: Repo, rid: str) -> dict:
    found = store.locate(repo, rid, ("processing",))
    if found is None:
        raise StewardError(f"{rid} is not being processed; claim it with `steward next --id {rid}`")
    return found[1]


def next_request(repo: Repo, cfg: Config, rid: Optional[str] = None) -> Tuple[Optional[dict], List[dict]]:
    """Claim a request. Returns (claimed or None, requests found superseded along the way)."""
    busy = store.list_requests(repo, "processing")
    if busy:
        raise StewardError(f"already processing {busy[0]['id']}; merge, reject, hold or requeue it first")
    queue = store.list_requests(repo, "queue")
    if rid is not None:
        queue = [r for r in queue if r["id"] == rid]
        if not queue:
            raise StewardError(f"{rid} is not in the queue")
    queue.sort(key=lambda r: (not r.get("approved"), not r.get("requeued"), r["submittedAt"], r["id"]))
    superseded: List[dict] = []
    for req in queue:
        current = gitutil.rev(f"refs/heads/{req['branch']}", repo.main_checkout)
        if current != req["head"]:
            store.move(repo, req["id"], "queue", "rejected", rejectKind="superseded",
                       currentHead=current, rejectedAt=store.now_iso())
            events.record(repo, "superseded", req)
            superseded.append(req)
            continue
        return store.move(repo, req["id"], "queue", "processing", claimedAt=store.now_iso()), superseded
    return None, superseded


def _worktree_branches(repo: Repo) -> List[Tuple[str, str]]:
    out = gitutil.git(["worktree", "list", "--porcelain"], repo.main_checkout)
    result: List[Tuple[str, str]] = []
    path = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch refs/heads/") and path is not None:
            result.append((line[len("branch refs/heads/"):], path))
    return result


def _session_name_for(repo: Repo, branch: str) -> Optional[str]:
    reqs = [r for s in store.STATES for r in store.list_requests(repo, s) if r["branch"] == branch and r.get("sessionName")]
    return max(reqs, key=lambda r: r["submittedAt"])["sessionName"] if reqs else None


def review(repo: Repo, cfg: Config, rid: str) -> dict:
    req = processing(repo, rid)
    top = repo.main_checkout
    integ = gitutil.git(["rev-parse", cfg.integration], top)
    diff_path = repo.path("logs", f"{rid}.diff")
    diff_args = ["diff", f"{integ}...{req['head']}"]
    p = gitutil.run(diff_args, top)
    if p.returncode != 0:
        raise gitutil.GitError(diff_args, p.returncode, p.stdout, p.stderr)
    with open(diff_path, "w", encoding="utf-8") as f:
        f.write(p.stdout)
    mine = set(req["files"])
    since_base = set(gitutil.zpaths(gitutil.git(["diff", "--name-only", "-z", req["integrationBase"], integ], top)))
    overlap_merged = [
        {"id": d["id"], "branch": d["branch"], "files": sorted(mine & set(d["files"]))}
        for d in store.list_requests(repo, "done")
        if d.get("mergedAt", "") >= req["submittedAt"] and mine & set(d["files"])
    ]
    overlap_active = []
    for branch, path in _worktree_branches(repo):
        if branch in (req["branch"], cfg.integration, cfg.main):
            continue
        theirs = set(gitutil.zpaths(gitutil.git(["diff", "--name-only", "-z", f"{integ}...{branch}"], top)))
        common = mine & theirs
        if common:
            overlap_active.append({"branch": branch, "worktree": path,
                                   "sessionName": _session_name_for(repo, branch), "files": sorted(common)})
    return {
        "request": req,
        "integration": integ,
        "diffstat": gitutil.git(["diff", "--stat", f"{integ}...{req['head']}"], top),
        "diffPath": diff_path,
        "integrationChangedSinceBase": sorted(mine & since_base),
        "overlapWithMerged": overlap_merged,
        "overlapWithActive": overlap_active,
    }
