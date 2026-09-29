"""Entry point: `steward <command>`. Each cmd_* module registers its own subcommands."""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import cmd_decide, cmd_hooks, cmd_lifecycle, cmd_report, cmd_review, cmd_verify, cmd_worker
from .config import load_config
from .errors import StewardError
from .paths import find_repo

MODULES = (cmd_worker, cmd_hooks, cmd_lifecycle, cmd_review, cmd_verify, cmd_decide, cmd_report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="steward", description="Merge steward for parallel Claude Code sessions")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for module in MODULES:
        module.register(sub)
    return parser


def main(argv: Optional[List[str]] = None, cwd: Optional[str] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not getattr(args, "needs_repo", True):
            return args.func(args)
        repo = find_repo(cwd or os.getcwd())
        if repo is None:
            raise StewardError("not inside a git repository")
        cfg = load_config(repo)
        if cfg is None:
            raise StewardError("no .claude/steward.json in this repository; the merge steward is not enabled here")
        return args.func(repo, cfg, args)
    except StewardError as e:
        print(f"steward: {e}", file=sys.stderr)
        return 1
