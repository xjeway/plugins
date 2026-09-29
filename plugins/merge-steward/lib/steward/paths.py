"""Locate the repository, its shared git dir and the steward state directory."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from . import gitutil

STATES = ("queue", "processing", "awaiting", "done", "rejected")


def safe_name(branch: str) -> str:
    return branch.replace("/", "__")


@dataclass
class Repo:
    toplevel: str
    common_dir: str

    @property
    def state_dir(self) -> str:
        return os.path.join(self.common_dir, "steward")

    @property
    def verify_dir(self) -> str:
        """The steward's verify worktree: a sibling of the main checkout (/x/repo -> /x/repo.steward-verify).

        It must not live inside the main checkout, or tools that resolve upward (node's require, eslint,
        tsconfig) would find the main checkout's node_modules and config, and a branch missing a
        dependency could pass verify.
        """
        return os.path.normpath(self.main_checkout) + ".steward-verify"

    @property
    def main_checkout(self) -> str:
        if os.path.basename(self.common_dir) == ".git":
            return os.path.dirname(self.common_dir)
        return self.toplevel

    def path(self, *parts: str) -> str:
        return os.path.join(self.state_dir, *parts)

    def branch(self) -> Optional[str]:
        p = gitutil.run(["symbolic-ref", "-q", "--short", "HEAD"], self.toplevel)
        if p.returncode != 0:
            return None
        return p.stdout.strip() or None

    def is_verify_worktree(self) -> bool:
        return os.path.realpath(self.toplevel) == os.path.realpath(self.verify_dir)

    def ensure_dirs(self) -> None:
        for d in (*STATES, "inbox", os.path.join("inbox", "acked"), "logs", "asked"):
            os.makedirs(self.path(d), exist_ok=True)


def find_repo(cwd: str) -> Optional[Repo]:
    p = gitutil.run(["rev-parse", "--show-toplevel", "--path-format=absolute", "--git-common-dir"], cwd)
    if p.returncode != 0:
        return None
    top, common = p.stdout.strip().splitlines()[:2]
    return Repo(toplevel=os.path.realpath(top), common_dir=os.path.realpath(common))
