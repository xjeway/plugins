"""Steward commands: next, review."""
from __future__ import annotations

import json

from . import review


def register(sub) -> None:
    p = sub.add_parser("next", help="claim the next request (approved first, then requeued, then FIFO)")
    p.add_argument("--id")
    p.set_defaults(func=_next)

    p = sub.add_parser("review", help="print review material for the request being processed")
    p.add_argument("id")
    p.set_defaults(func=_review)


def _next(repo, cfg, args) -> int:
    req, superseded = review.next_request(repo, cfg, args.id)
    for s in superseded:
        print(f"superseded {s['id']} ({s['branch']} moved after submission)")
    if req is None:
        print("queue empty")
        return 0
    print(json.dumps(req, ensure_ascii=False, indent=2))
    return 0


def _review(repo, cfg, args) -> int:
    print(json.dumps(review.review(repo, cfg, args.id), ensure_ascii=False, indent=2))
    return 0
