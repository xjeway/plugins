"""Shared fixtures: throwaway git repositories with worktrees."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))

DEFAULT_CONFIG = {"verify": ["true"]}


def sh(args, cwd):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise AssertionError(f"{args} failed in {cwd}: {p.stderr}{p.stdout}")
    return p.stdout.strip()


class RepoTestCase(unittest.TestCase):
    config = DEFAULT_CONFIG

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="steward-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(self.root)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "commit.gpgsign", "false")
        self.write_config(self.config)
        self.commit("README.md", "hello\n", "initial")

    def git(self, *args, cwd=None):
        return sh(["git", *args], cwd or self.root)

    def write(self, rel, content, cwd=None):
        path = os.path.join(cwd or self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def commit(self, rel, content, msg, cwd=None):
        self.write(rel, content, cwd)
        self.git("add", "-A", cwd=cwd)
        self.git("commit", "-q", "-m", msg, cwd=cwd)
        return self.git("rev-parse", "HEAD", cwd=cwd)

    def write_config(self, cfg):
        self.write(".claude/steward.json", json.dumps(cfg))

    def make_integration(self):
        self.git("branch", "integration", "main")

    def worktree(self, branch, base="main"):
        path = os.path.join(self.tmp, branch.replace("/", "-"))
        self.git("worktree", "add", "-q", "-b", branch, path, base)
        return path

    def repo(self, cwd=None):
        from steward.paths import find_repo
        return find_repo(cwd or self.root)

    def cfg(self):
        from steward.config import load_config
        return load_config(self.repo())

    def cli(self, *argv, cwd=None):
        from steward.cli import main
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv), cwd=cwd or self.root)
        return code, out.getvalue(), err.getvalue()

    def ok(self, *argv, cwd=None):
        code, out, err = self.cli(*argv, cwd=cwd)
        self.assertEqual(code, 0, f"steward {' '.join(argv)} failed: {err}")
        return out

    def init_steward(self):
        return self.ok("init", "--session-name", "Merge steward")

    def feature(self, branch, rel, content, msg=None):
        """Create a worktree on `branch` with one commit, and submit it. Returns (worktree path, request)."""
        wt = self.worktree(branch)
        self.commit(rel, content, msg or f"change {rel}", cwd=wt)
        self.ok("submit", "--session-name", branch, cwd=wt)
        from steward import store
        return wt, store.locate(self.repo(), branch, ("queue",))[1]
