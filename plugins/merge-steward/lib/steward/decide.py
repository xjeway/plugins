"""The steward's decisions on a request: merge, reject, hold for a human, approve, deny, requeue."""
from __future__ import annotations

import os
from typing import Optional

from . import events, gitutil, inbox, store, worktree
from .config import Config
from .errors import StewardError
from .paths import Repo
from .review import processing

REJECT_KINDS = ("conflict", "verify-failed", "review")
EVIDENCE_TAIL_LINES = 200


def needs_approval(cfg: Config, req: dict) -> bool:
    if cfg.approval_mode == "never":
        return False
    if cfg.approval_mode == "always":
        return True
    risk = req["risk"]
    return bool(risk["paths"] or risk["deletions"] or risk["notes"])


def merge_commit_for(repo: Repo, cfg: Config, req: dict) -> Optional[str]:
    """The commit on integration whose Steward-Request trailer is exactly this request's id and that contains its head."""
    last = req.get("lastVerify") or {}
    rng = [f"refs/heads/{cfg.integration}"]
    if last.get("integration"):
        rng.append(f"^{last['integration']}")  # a merge made after verify sits above the verified tip
    out = gitutil.git(
        ["log", "--format=%x01%H%x00%(trailers:key=Steward-Request,valueonly)", *rng], repo.main_checkout
    )
    for chunk in out.split("\x01"):
        sha, _, trailers = chunk.partition("\x00")
        if sha and req["id"] in [t.strip() for t in trailers.splitlines()]:
            if gitutil.run(["merge-base", "--is-ancestor", req["head"], sha.strip()], repo.main_checkout).returncode == 0:
                return sha.strip()
    return None


def complete_merge(repo: Repo, cfg: Config, rid: str, merge_commit: str) -> dict:
    """Bookkeeping after the merge commit exists: clean the worktree, move to done, count it, record it."""
    worktree.reset(repo, cfg)
    req = store.move(repo, rid, "processing", "done", mergedAt=store.now_iso(), mergeCommit=merge_commit)
    state = store.read_state(repo)
    state["mergesSincePromote"] += 1
    store.write_state(repo, state)
    events.record(repo, "merged", req)
    req["promoteDue"] = state["mergesSincePromote"] >= cfg.every_n_merges
    return req


def merge(repo: Repo, cfg: Config, rid: str) -> dict:
    req = processing(repo, rid)
    already = merge_commit_for(repo, cfg, req)
    if already:  # a previous run committed but died before finishing the bookkeeping
        return complete_merge(repo, cfg, rid, already)
    last = req.get("lastVerify") or {}
    if last.get("result") != "PASS":
        raise StewardError("the last verify did not PASS; run `steward verify` first")
    if gitutil.rev(f"refs/heads/{req['branch']}", repo.main_checkout) != req["head"]:
        raise StewardError("the branch moved after it was submitted; run `steward requeue` and it will be superseded")
    if gitutil.rev(f"refs/heads/{cfg.integration}", repo.main_checkout) != last["integration"]:
        raise StewardError(f"{cfg.integration} moved since verify; run `steward verify` again")
    if needs_approval(cfg, req) and not req.get("approved"):
        raise StewardError("this request needs human approval; run `steward hold` and ask the user")
    d = repo.verify_dir
    if gitutil.rev("MERGE_HEAD", d) != req["head"]:
        raise StewardError("the verify worktree no longer holds this merge; run `steward verify` again")
    message = f"Merge {req['branch']} into {cfg.integration}\n\n{req['summary']}\n\nSteward-Request: {rid}"
    gitutil.git(["commit", "--no-verify", "-q", "-m", message], d)
    return complete_merge(repo, cfg, rid, gitutil.git(["rev-parse", "HEAD"], d))


def _tail(path: str, n: int) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        return "".join(f.readlines()[-n:])


def evidence_for(req: dict) -> str:
    last = req.get("lastVerify") or {}
    if last.get("result") == "CONFLICT":
        return _tail(last["evidence"], 10_000)
    if last.get("result") in ("FAIL", "TIMEOUT"):
        return f"$ {last['command']}\n" + _tail(last["log"], EVIDENCE_TAIL_LINES)
    return ""


def reject(repo: Repo, cfg: Config, rid: str, kind: str, reason: str) -> dict:
    if kind not in REJECT_KINDS:
        raise StewardError(f"kind must be one of {', '.join(REJECT_KINDS)}")
    req = processing(repo, rid)
    inbox.write_notice(repo, req, kind, reason, evidence_for(req))
    worktree.reset(repo, cfg)
    req = store.move(repo, rid, "processing", "rejected", rejectKind=kind, rejectedAt=store.now_iso())
    events.record(repo, "rejected", req, kind=kind)
    return req


def hold(repo: Repo, cfg: Config, rid: str, note: str) -> dict:
    req = processing(repo, rid)
    req["risk"]["notes"].append(note)
    store.save(repo, "processing", req)
    worktree.reset(repo, cfg)
    req = store.move(repo, rid, "processing", "awaiting", heldAt=store.now_iso(), lastVerify=None)
    events.record(repo, "held", req, note=note)
    return req


def _awaiting(repo: Repo, ref: str) -> dict:
    found = store.locate(repo, ref, ("awaiting",))
    if found is None:
        raise StewardError(f"no request awaiting approval matches {ref}")
    return found[1]


def approve(repo: Repo, ref: str, by: str = "user") -> dict:
    req = _awaiting(repo, ref)
    req = store.move(repo, req["id"], "awaiting", "queue", approved={"by": by, "at": store.now_iso()})
    events.record(repo, "approved", req, by=by)
    return req


def deny(repo: Repo, ref: str, reason: str) -> dict:
    req = _awaiting(repo, ref)
    inbox.write_notice(repo, req, "denied", reason, "")
    req = store.move(repo, req["id"], "awaiting", "rejected", rejectKind="denied", rejectedAt=store.now_iso())
    events.record(repo, "rejected", req, kind="denied")
    return req


def requeue(repo: Repo, cfg: Config, rid: str) -> dict:
    processing(repo, rid)
    worktree.reset(repo, cfg)
    return store.move(repo, rid, "processing", "queue", requeued=True, lastVerify=None)


def log_event(repo: Repo, type_: str, rid: Optional[str], text: str) -> None:
    if type_ not in ("coordinated", "human-requested"):
        raise StewardError("event type must be coordinated or human-requested")
    req = None
    if rid:
        found = store.locate(repo, rid)
        if found is None:
            raise StewardError(f"unknown request {rid}")
        req = found[1]
    events.record(repo, type_, req, text=text)
