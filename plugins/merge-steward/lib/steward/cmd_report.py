"""Steward commands: promote, metrics."""
from __future__ import annotations

import json

from . import events, promote


def register(sub) -> None:
    p = sub.add_parser("promote", help="push integration and open (or refresh) the PR into main")
    p.set_defaults(func=_promote)

    p = sub.add_parser("metrics", help="conflict, reject, human-intervention and first-pass rates")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_metrics)


def _promote(repo, cfg, args) -> int:
    print(promote.promote(repo, cfg))
    return 0


def _metrics(repo, cfg, args) -> int:
    m = events.metrics(events.load(repo))
    if args.json:
        print(json.dumps(m, indent=2))
    else:
        for key, value in m.items():
            print(f"{key}: {'-' if value is None else value}")
    return 0
