"""Thin wrappers around the git CLI."""
from __future__ import annotations

import os
import subprocess
from typing import Dict, List, Optional

from .errors import StewardError


class GitError(StewardError):
    def __init__(self, args: List[str], code: int, out: str, err: str):
        super().__init__(f"git {' '.join(args)} failed ({code}): {(err or out).strip()}")
        self.code = code
        self.out = out
        self.err = err


def run(args: List[str], cwd: str, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    """Run git, decoding its output as UTF-8. Bytes that are not UTF-8 (Latin-1, UTF-16 content) become U+FFFD."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=dict(os.environ, **env) if env else None)


def run_bytes(args: List[str], cwd: str) -> subprocess.CompletedProcess:
    """Run git and keep its stdout as raw bytes, for file content that must round-trip exactly."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True)


def git(args: List[str], cwd: str) -> str:
    p = run(args, cwd)
    if p.returncode != 0:
        raise GitError(args, p.returncode, p.stdout, p.stderr)
    return p.stdout.strip()


def rev(ref: str, cwd: str) -> Optional[str]:
    """Commit sha for `ref`, or None if it does not exist."""
    p = run(["rev-parse", "-q", "--verify", f"{ref}^{{commit}}"], cwd)
    return p.stdout.strip() if p.returncode == 0 else None


def lines(out: str) -> List[str]:
    return [line for line in out.splitlines() if line.strip()]


def zpaths(out: str) -> List[str]:
    """Split NUL-separated (`-z`) path output. Paths come back verbatim, not quoted per core.quotePath."""
    return [p for p in out.split("\0") if p]


def conflicted(cwd: str) -> List[str]:
    """Paths with unresolved conflicts in the index."""
    args = ["diff", "--name-only", "-z", "--diff-filter=U"]
    p = run(args, cwd)
    if p.returncode != 0:
        raise GitError(args, p.returncode, p.stdout, p.stderr)
    return zpaths(p.stdout)
