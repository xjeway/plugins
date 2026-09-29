"""Stop and UserPromptSubmit hooks for worker sessions. A hook must never block a worker by crashing."""
from __future__ import annotations

import json
import os
import traceback
from typing import Optional, Tuple

from . import gitutil, inbox
from .config import Config, load_config
from .paths import Repo, find_repo, safe_name
from .store import now_iso
from .submit import check_submittable

ASK = (
    "This branch has commits that have not been submitted to the merge steward.\n"
    "If your task is finished and the tests pass, submit it by following the merge-steward:submit skill "
    "(it runs `steward submit`).\n"
    "If the task is not finished yet, just stop; you will not be asked again for this commit."
)

HookResult = Tuple[int, str, str]


def _worker_context(cwd: str) -> Optional[Tuple[Repo, Config, str]]:
    repo = find_repo(cwd)
    if repo is None or repo.is_verify_worktree():
        return None
    cfg = load_config(repo)
    if cfg is None:
        return None
    branch = repo.branch()
    if branch is None or branch in (cfg.integration, cfg.main):
        return None
    return repo, cfg, branch


def _asked_path(repo: Repo, branch: str) -> str:
    return repo.path("asked", safe_name(branch))


def _already_asked(repo: Repo, branch: str, head: str) -> bool:
    p = _asked_path(repo, branch)
    if not os.path.exists(p):
        return False
    with open(p, encoding="utf-8") as f:
        return f.read().strip() == head


def _mark_asked(repo: Repo, branch: str, head: str) -> None:
    repo.ensure_dirs()
    with open(_asked_path(repo, branch), "w", encoding="utf-8") as f:
        f.write(head + "\n")


def stop(payload: dict) -> HookResult:
    ctx = _worker_context(payload.get("cwd") or os.getcwd())
    if ctx is None:
        return 0, "", ""
    repo, cfg, branch = ctx
    active = bool(payload.get("stop_hook_active"))
    notice = inbox.pending(repo, branch)
    if notice and not active:
        return 2, "", inbox.format_notice(notice)
    if active:
        return 0, "", ""
    ok, _ = check_submittable(repo, cfg)
    if not ok:
        return 0, "", ""
    head = gitutil.git(["rev-parse", "HEAD"], repo.toplevel)
    if _already_asked(repo, branch, head):
        return 0, "", ""
    _mark_asked(repo, branch, head)
    return 2, "", ASK


def prompt(payload: dict) -> HookResult:
    ctx = _worker_context(payload.get("cwd") or os.getcwd())
    if ctx is None:
        return 0, "", ""
    repo, _, branch = ctx
    notice = inbox.pending(repo, branch)
    if notice is None:
        return 0, "", ""
    out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": inbox.format_notice(notice)}}
    return 0, json.dumps(out, ensure_ascii=False), ""


def _log_failure(payload: dict, tb: str) -> None:
    try:
        repo = find_repo(payload.get("cwd") or os.getcwd())
        if repo is not None:
            os.makedirs(repo.state_dir, exist_ok=True)
            with open(repo.path("hook.log"), "a", encoding="utf-8") as f:
                f.write(f"{now_iso()}\n{tb}\n")
    except Exception:
        pass


def run(kind: str, stdin_text: str) -> HookResult:
    payload: dict = {}
    try:
        parsed = json.loads(stdin_text or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("hook payload is not a JSON object")
        payload = parsed
        return stop(payload) if kind == "stop" else prompt(payload)
    except Exception:
        _log_failure(payload, traceback.format_exc())
        return 0, "", ""
