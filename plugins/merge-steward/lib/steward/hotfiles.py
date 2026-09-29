"""Resolve conflicts in configured hot files, wherever a merge or rebase stopped (verify worktree or a worker's).

In both places stage 2 ("ours") is the integration side and stage 3 ("theirs") is the worker's change:
the verify worktree merges the worker's head into integration, and a rebase onto integration replays the
worker's commits on top of it.
"""
from __future__ import annotations

import codecs
import fnmatch
import os
import signal
import subprocess
import tempfile
from typing import List, Optional, Tuple

from . import gitutil
from .config import Config

_UTF16_BOMS = ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"))


def rule_for(cfg: Config, path: str) -> Optional[str]:
    return next((r for pat, r in cfg.hot_files.items() if fnmatch.fnmatch(path, pat)), None)


def kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def _show(d: str, spec: str) -> Optional[bytes]:
    p = gitutil.run_bytes(["show", spec], d)
    return p.stdout if p.returncode == 0 else None


def _stage(d: str, n: int, path: str) -> Optional[bytes]:
    return _show(d, f":{n}:{path}")


def _utf16_codec(data: bytes) -> Optional[str]:
    return next((codec for bom, codec in _UTF16_BOMS if data.startswith(bom)), None)


def _utf16_to_utf8(data: bytes) -> bytes:
    """git treats UTF-16 as binary and will not merge it, so the union runs on a UTF-8 copy."""
    if not data:
        return b""
    codec = _utf16_codec(data)
    if codec is None:
        raise UnicodeError("one side of a UTF-16 file has no byte-order mark")
    return data[2:].decode(codec).encode("utf-8")


def _union(d: str, path: str, against: Optional[Tuple[str, str]] = None) -> bool:
    """Union integration's and the worker's lines of `path`.

    `against` = (integration commit, fork point) is for a rebase: there stage 1 is the worker's previous commit
    and stage 2 already holds the worker's earlier versions, so a union of the stages would bring back lines
    the worker rewrote. Instead ours is integration's copy and base the fork point's, with the replayed commit
    as theirs - the same three sides as verify's single trial merge.
    """
    theirs = _stage(d, 3, path)
    if against is None:
        ours, base = _stage(d, 2, path), _stage(d, 1, path)
    else:
        ours, base = _show(d, f"{against[0]}:{path}"), _show(d, f"{against[1]}:{path}")
    if ours is None or theirs is None:
        return False
    base = base or b""
    out_codec = _utf16_codec(ours) or _utf16_codec(theirs)
    sides = (ours, base, theirs)
    if out_codec is not None:
        sides = tuple(_utf16_to_utf8(s) for s in sides)
    with tempfile.TemporaryDirectory() as t:
        files = []
        for name, content in zip(("ours", "base", "theirs"), sides):
            fp = os.path.join(t, name)
            with open(fp, "wb") as f:
                f.write(content)
            files.append(fp)
        p = subprocess.run(["git", "merge-file", "--union", "-p", *files], capture_output=True)
    if p.returncode != 0:
        return False
    merged = p.stdout
    if out_codec is not None:
        bom = next(b for b, c in _UTF16_BOMS if c == out_codec)
        merged = bom + merged.decode("utf-8").encode(out_codec)
    with open(os.path.join(d, path), "wb") as f:
        f.write(merged)
    gitutil.git(["add", "--", path], d)
    return True


def _regenerate(d: str, path: str, command: str, timeout: float) -> bool:
    gitutil.git(["checkout", "--ours", "--", path], d)
    proc = subprocess.Popen(command, shell=True, cwd=d, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_group(proc)
        return False
    except BaseException:  # interrupted (a signal handler raised): do not leave the command running
        kill_group(proc)
        raise
    if code != 0:
        return False
    gitutil.git(["add", "--", path], d)
    return True


def resolve(cfg: Config, d: str, path: str, against: Optional[Tuple[str, str]] = None) -> bool:
    """Apply `path`'s hot-file rule and stage the result. False if it has no rule or the rule failed
    (rule failure, timeout, git error, undecodable text). `against`: see _union (rebase only)."""
    rule = rule_for(cfg, path)
    try:
        if rule == "union":
            return _union(d, path, against)
        if rule and rule.startswith("regenerate:"):
            return _regenerate(d, path, rule[len("regenerate:"):].strip(), cfg.verify_timeout)
    except (gitutil.GitError, OSError, UnicodeError):
        pass
    return False


def resolve_hot_files(cfg: Config, d: str) -> List[str]:
    """Resolve conflicts in configured hot files. Returns the paths that are still conflicted."""
    return [path for path in gitutil.conflicted(d) if not resolve(cfg, d, path)]
