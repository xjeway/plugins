"""The steward's private verify worktree, which always has the integration branch checked out."""
from __future__ import annotations

import os

from . import gitutil
from .config import Config
from .errors import StewardError
from .paths import Repo


def ensure(repo: Repo, cfg: Config) -> None:
    """Create the verify worktree, or check that what is already there is it. The steward resets and cleans
    this directory, so anything else found at its path is refused and left alone."""
    d = repo.verify_dir
    if os.path.exists(os.path.join(d, ".git")):
        p = gitutil.run(["rev-parse", "--path-format=absolute", "--git-common-dir"], d)
        if p.returncode != 0 or os.path.realpath(p.stdout.strip()) != os.path.realpath(repo.common_dir):
            raise StewardError(f"{d} exists but is not a worktree of this repository; move it away and run init again")
        branch = gitutil.run(["symbolic-ref", "-q", "--short", "HEAD"], d).stdout.strip()
        if branch != cfg.integration:
            raise StewardError(f"{d} is a worktree of this repository but has {branch or 'a detached HEAD'} checked "
                               f"out, not {cfg.integration}; remove it (`git worktree remove`) and run init again")
        return
    gitutil.run(["worktree", "prune"], repo.main_checkout)
    gitutil.git(["worktree", "add", repo.verify_dir, cfg.integration], repo.main_checkout)


def reset(repo: Repo, cfg: Config) -> None:
    """Discard any merge in progress and leftovers from verify commands (ignored files are kept)."""
    d = repo.verify_dir
    gitutil.run(["merge", "--abort"], d)
    gitutil.git(["reset", "-q", "--hard", cfg.integration], d)
    gitutil.git(["clean", "-q", "-fd"], d)


def sync_main(repo: Repo, cfg: Config, source: str) -> bool:
    """Merge `source` (main or origin/main) into integration. Returns False, leaving things clean, on conflict."""
    reset(repo, cfg)
    p = gitutil.run(["merge", "--no-edit", source], repo.verify_dir)
    if p.returncode != 0:
        reset(repo, cfg)
        return False
    return True
