"""Steward commands: init, watch, status."""
from __future__ import annotations

import json

from . import lifecycle


def register(sub) -> None:
    p = sub.add_parser("init", help="start (or resume) being the steward for this repository")
    p.add_argument("--session-name", required=True)
    p.set_defaults(func=_init)

    p = sub.add_parser("watch", help="print a line per new queue entry; run under the Monitor tool")
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--once", action="store_true")
    p.set_defaults(func=_watch)

    p = sub.add_parser("status", help="show queue, processing, awaiting approval and recent results")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_status)


def _init(repo, cfg, args) -> int:
    for message in lifecycle.init(repo, cfg, args.session_name):
        print(message)
    return 0


def _watch(repo, cfg, args) -> int:
    lifecycle.watch(repo, args.interval, once=args.once)
    return 0


def _status(repo, cfg, args) -> int:
    s = lifecycle.status(repo, cfg)
    print(json.dumps(s, ensure_ascii=False, indent=2) if args.json else lifecycle.format_status(s))
    return 0
