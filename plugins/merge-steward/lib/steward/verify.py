"""Trial-merge a request into integration in the verify worktree and run the verify commands."""
from __future__ import annotations

import subprocess
import time
from typing import Dict, List, Optional

from . import events, gitutil, store, worktree
from .config import Config
from .errors import StewardError
from .hotfiles import kill_group, resolve_hot_files
from .paths import Repo
from .review import processing


def run_commands(commands: List[str], cwd: str, log_path: str, timeout: float) -> Dict[str, Optional[str]]:
    deadline = time.monotonic() + timeout
    with open(log_path, "w", encoding="utf-8") as log:
        for cmd in commands:
            log.write(f"$ {cmd}\n")
            log.flush()
            proc = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            try:
                code = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                kill_group(proc)
                log.write(f"\n[steward] timed out after {timeout}s\n")
                return {"result": "TIMEOUT", "command": cmd}
            if code != 0:
                log.write(f"\n[steward] exit code {code}\n")
                return {"result": "FAIL", "command": cmd}
    return {"result": "PASS", "command": None}


def run_verify(repo: Repo, cfg: Config, rid: str) -> dict:
    req = processing(repo, rid)
    worktree.reset(repo, cfg)
    d = repo.verify_dir
    result: dict = {
        "integration": gitutil.git(["rev-parse", "HEAD"], d),
        "head": req["head"],
        "at": store.now_iso(),
        "log": repo.path("logs", f"{rid}.log"),
        "files": [],
    }
    p = gitutil.run(["merge", "--no-ff", "--no-commit", req["head"]], d)
    if p.returncode != 0:
        conflicted = gitutil.conflicted(d)
        if not conflicted:
            worktree.reset(repo, cfg)
            raise StewardError(f"merge failed without conflicts: {(p.stderr or p.stdout).strip()}")
        # Capture evidence first: resolving a hot file overwrites its conflict hunks.
        evidence = {path: gitutil.run(["diff", "--", path], d).stdout for path in conflicted}
        unresolved = resolve_hot_files(cfg, d)
        if unresolved:
            evidence_path = repo.path("logs", f"{rid}.conflict")
            with open(evidence_path, "w", encoding="utf-8") as f:
                f.write("".join(evidence.get(path, "") for path in unresolved))
            worktree.reset(repo, cfg)
            result.update(result="CONFLICT", command=None, files=unresolved, evidence=evidence_path)
            return _finish(repo, req, result)
    elif gitutil.rev("MERGE_HEAD", d) is None:
        worktree.reset(repo, cfg)
        raise StewardError(f"nothing to merge: {req['branch']} is already contained in {cfg.integration}")
    result.update(run_commands(cfg.verify, d, result["log"], cfg.verify_timeout))
    return _finish(repo, req, result)


def _finish(repo: Repo, req: dict, result: dict) -> dict:
    req["lastVerify"] = result
    store.save(repo, "processing", req)
    events.record(repo, "verified", req, result=result["result"])
    return result


def verify_base(repo: Repo, cfg: Config) -> dict:
    """Run the verify commands on integration alone, to tell a broken integration from a broken branch."""
    worktree.reset(repo, cfg)
    log_path = repo.path("logs", f"base-{time.strftime('%Y%m%dT%H%M%S')}.log")
    result = run_commands(cfg.verify, repo.verify_dir, log_path, cfg.verify_timeout)
    result["log"] = log_path
    return result
