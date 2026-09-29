"""Worker side: decide whether a branch can be submitted, rebase it, and enqueue it."""
from __future__ import annotations

import fnmatch
import re
import signal
from contextlib import contextmanager
from typing import Iterator, List, Optional, Tuple

from . import events, gitutil, hotfiles, inbox, store
from .config import Config
from .errors import StewardError
from .paths import Repo

ISSUE_RE = re.compile(r"(?:#|\b(?:issue|gh)[-_/]?)(\d+)\b", re.IGNORECASE)


class SubmitError(StewardError):
    pass


def extract_issues(texts: List[str]) -> List[int]:
    found: List[int] = []
    for text in texts:
        for m in ISSUE_RE.finditer(text):
            n = int(m.group(1))
            if n not in found:
                found.append(n)
    return found


def parse_name_status_z(out: str) -> Tuple[List[str], List[str]]:
    """Parse `git diff --name-status -z`: status, NUL, path, NUL; renames and copies carry two paths
    (source, destination). A rename counts as deleting its source."""
    fields = out.split("\0")
    files: List[str] = []
    deleted: List[str] = []
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        if status[0] in "RC":
            src, dst = fields[i + 1], fields[i + 2]
            files.extend([src, dst] if status[0] == "R" else [dst])
            if status[0] == "R":
                deleted.append(src)
            i += 3
        else:
            files.append(fields[i + 1])
            if status.startswith("D"):
                deleted.append(fields[i + 1])
            i += 2
    return files, deleted


def changed_files(repo: Repo, base: str, head: str) -> Tuple[List[str], List[str]]:
    return parse_name_status_z(gitutil.git(["diff", "--name-status", "-z", "--no-renames", base, head], repo.toplevel))


def assess_risk(cfg: Config, files: List[str], deleted: List[str]) -> dict:
    paths = [f for f in files if any(fnmatch.fnmatch(f, pat) for pat in cfg.risky_paths)]
    return {"paths": paths, "deletions": bool(deleted) and cfg.risky_when_deleting, "notes": []}


def check_submittable(repo: Repo, cfg: Config) -> Tuple[bool, str]:
    branch = repo.branch()
    if branch is None:
        return False, "HEAD is detached"
    if branch in (cfg.integration, cfg.main):
        return False, f"on {branch}; submit from a work branch"
    if gitutil.rev(f"refs/heads/{cfg.integration}", repo.toplevel) is None:
        return False, f"branch {cfg.integration} does not exist; the steward has not run `steward init`"
    if gitutil.git(["status", "--porcelain", "--untracked-files=no"], repo.toplevel):
        return False, "working tree has uncommitted changes"
    if int(gitutil.git(["rev-list", "--count", f"{cfg.integration}..HEAD"], repo.toplevel)) == 0:
        return False, f"no commits ahead of {cfg.integration}"
    if store.head_known(repo, gitutil.git(["rev-parse", "HEAD"], repo.toplevel)):
        return False, "this HEAD was already submitted"
    return True, ""


class SubmitInterrupted(SubmitError):
    pass


_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


def _raise_interrupted(signum, frame) -> None:
    raise SubmitInterrupted(f"interrupted by {signal.Signals(signum).name}; the rebase was aborted and the branch "
                            "is unchanged")


@contextmanager
def _signals(handler) -> Iterator[None]:
    """Route SIGTERM/SIGINT/SIGHUP to `handler` for the duration, restoring the previous handlers after."""
    previous = {}
    try:
        for sig in _SIGNALS:
            previous[sig] = signal.signal(sig, handler)
    except ValueError:  # not the main thread: signals cannot be handled here
        pass
    try:
        yield
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)


def _abort_rebase(repo: Repo) -> None:
    gitutil.run(["rebase", "--abort"], repo.toplevel)


def _conflict_error(cfg: Config, conflicted: List[str], detail: str = "") -> SubmitError:
    return SubmitError(
        f"rebase onto {cfg.integration} hit conflicts in: {', '.join(conflicted)}{detail}\n"
        f"Run `git rebase {cfg.integration}`, resolve the conflicts, run the tests, then submit again."
    )


def rebase_onto_integration(repo: Repo, cfg: Config) -> List[str]:
    """Rebase the branch onto integration. Conflicts only in hot files are resolved by their rules, commit by
    commit; anything else aborts the rebase, leaving the branch as it was. Returns the hot files resolved.

    Any failure, including SIGTERM/SIGINT/SIGHUP (e.g. the caller's timeout), aborts the rebase: a worker must
    never be left mid-rebase on a detached HEAD.
    """
    with _signals(_raise_interrupted):
        try:
            return _rebase(repo, cfg)
        except BaseException:
            with _signals(signal.SIG_IGN):
                _abort_rebase(repo)
            raise


def _rebase(repo: Repo, cfg: Config) -> List[str]:
    top = repo.toplevel
    onto = gitutil.git(["rev-parse", cfg.integration], top)
    fork = gitutil.git(["merge-base", onto, "HEAD"], top)
    resolved: List[str] = []
    # Each stop is one replayed commit; the bound only guards against a rebase that never finishes.
    stops = int(gitutil.git(["rev-list", "--count", f"{onto}..HEAD"], top)) + 1
    p = gitutil.run(["rebase", onto], top)
    for _ in range(stops):
        if p.returncode == 0:
            return sorted(set(resolved))
        try:
            conflicted = gitutil.conflicted(top)
        except gitutil.GitError:
            conflicted = []
        if not conflicted:
            raise SubmitError(f"rebase onto {cfg.integration} failed: {(p.stderr or p.stdout).strip() or '(no output)'}")
        if any(hotfiles.rule_for(cfg, c) is None for c in conflicted):
            raise _conflict_error(cfg, conflicted)
        failed = [c for c in conflicted if not hotfiles.resolve(cfg, top, c, against=(onto, fork))]
        if failed:
            raise _conflict_error(cfg, conflicted, f" (the hotFiles rule failed for: {', '.join(failed)})")
        resolved.extend(conflicted)
        p = gitutil.run(["-c", "core.editor=true", "rebase", "--continue"], top, env={"GIT_EDITOR": "true"})
    raise SubmitError(f"rebase onto {cfg.integration} did not finish; the branch was left as it was")


def submit(repo: Repo, cfg: Config, session_id: Optional[str], session_name: Optional[str]) -> dict:
    ok, reason = check_submittable(repo, cfg)
    if not ok:
        raise SubmitError(reason)
    auto_resolved = rebase_onto_integration(repo, cfg)
    top = repo.toplevel
    head = gitutil.git(["rev-parse", "HEAD"], top)
    if store.head_known(repo, head):
        raise SubmitError("this HEAD was already submitted")
    # the commit the branch was actually rebased onto, even if integration moved during the rebase
    base = gitutil.git(["merge-base", cfg.integration, "HEAD"], top)
    branch = repo.branch() or ""
    summary = gitutil.git(["log", "--format=%s", f"{cfg.integration}..HEAD"], top)
    messages = gitutil.git(["log", "--format=%B", f"{cfg.integration}..HEAD"], top)
    files, deleted = changed_files(repo, base, head)
    repo.ensure_dirs()
    req = {
        "id": store.new_id(repo, branch),
        "branch": branch,
        "head": head,
        "integrationBase": base,
        "worktree": top,
        "sessionId": session_id,
        "sessionName": session_name,
        "issues": extract_issues([branch, messages]),
        "summary": summary,
        "files": files,
        "deletedFiles": deleted,
        "risk": assess_risk(cfg, files, deleted),
        "approved": None,
        "autoResolved": auto_resolved,
        "submittedAt": store.now_iso(),
    }
    store.save(repo, "queue", req)
    events.record(repo, "submitted", req)
    # A notice about an earlier request of this branch is answered by this submission; stop redelivering it.
    acked = inbox.ack(repo, branch)
    if acked is not None:
        req = dict(req, ackedNotice=acked["requestId"])
    return req
