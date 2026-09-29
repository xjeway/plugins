"""Open (or refresh) the integration -> main pull request."""
from __future__ import annotations

import os
import subprocess
from typing import List, Optional

from . import events, gitutil, store, worktree
from .config import Config
from .errors import StewardError
from .paths import Repo

REMOTE = "origin"


def _gh() -> str:
    return os.environ.get("STEWARD_GH", "gh")


def pr_body(repo: Repo, cfg: Config, since: Optional[str]) -> str:
    merged = [r for r in store.list_requests(repo, "done") if since is None or r.get("mergedAt", "") > since]
    rows: List[str] = []
    for r in merged:
        issues = " ".join(f"#{n}" for n in r.get("issues", []))
        title = r["summary"].splitlines()[0] if r["summary"] else ""
        rows.append(f"- `{r['branch']}` {title} {issues}".rstrip())
    return (
        "## Merged by the merge steward\n\n"
        + ("\n".join(rows) or "- (no new merges)")
        + f"\n\nPlease merge with a **merge commit** (not squash or rebase) so `{cfg.integration}` "
        f"and `{cfg.main}` keep a shared history.\n"
    )


def _run_gh(repo: Repo, args: List[str], cwd: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([_gh(), *args], cwd=cwd, capture_output=True, text=True)
    except OSError as e:
        raise _fail(repo, f"cannot run gh ({e}); install the GitHub CLI and run `gh auth login`") from e


def _fail(repo: Repo, message: str) -> StewardError:
    events.record(repo, "human-requested", reason="promote-failed", message=message)
    return StewardError(message)


def promote(repo: Repo, cfg: Config) -> str:
    top = repo.main_checkout
    if gitutil.run(["remote", "get-url", REMOTE], top).returncode != 0:
        raise _fail(repo, f"no '{REMOTE}' remote configured")
    try:
        gitutil.git(["fetch", "-q", REMOTE], top)
    except gitutil.GitError as e:
        raise _fail(repo, str(e)) from e
    source = f"{REMOTE}/{cfg.main}"
    if gitutil.rev(source, top) is not None and not worktree.sync_main(repo, cfg, source):
        events.record(repo, "human-requested", reason="main-merge-conflict")
        raise StewardError(f"merging {source} into {cfg.integration} conflicts; a human must resolve it")
    try:
        gitutil.git(["push", "-q", REMOTE, cfg.integration], top)
    except gitutil.GitError as e:
        raise _fail(repo, str(e)) from e
    listed = _run_gh(
        repo,
        ["pr", "list", "--head", cfg.integration, "--base", cfg.main, "--state", "open",
         "--json", "url", "--jq", ".[0].url"],
        top,
    )
    if listed.returncode != 0:
        raise _fail(repo, f"gh pr list failed: {listed.stderr.strip()}")
    url = listed.stdout.strip()
    state = store.read_state(repo)
    if not url:
        created = _run_gh(
            repo,
            ["pr", "create", "--base", cfg.main, "--head", cfg.integration,
             "--title", f"Promote {cfg.integration} into {cfg.main}",
             "--body", pr_body(repo, cfg, state.get("lastPromoteAt"))],
            top,
        )
        if created.returncode != 0:
            raise _fail(repo, f"gh pr create failed: {created.stderr.strip()}")
        url = created.stdout.strip().splitlines()[-1]
    state["mergesSincePromote"] = 0
    state["lastPromoteAt"] = store.now_iso()
    store.write_state(repo, state)
    events.record(repo, "promoted", url=url)
    return url
