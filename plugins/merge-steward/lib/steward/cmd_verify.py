"""Steward commands: verify, verify-base."""
from __future__ import annotations

from . import verify


def register(sub) -> None:
    p = sub.add_parser("verify", help="trial-merge the request into integration and run the verify commands")
    p.add_argument("id")
    p.set_defaults(func=_verify)

    p = sub.add_parser("verify-base", help="run the verify commands on integration alone")
    p.set_defaults(func=_verify_base)


def _verify(repo, cfg, args) -> int:
    r = verify.run_verify(repo, cfg, args.id)
    if r["result"] == "CONFLICT":
        print(f"CONFLICT {' '.join(r['files'])}")
        print(f"evidence: {r['evidence']}")
    else:
        print(r["result"] + (f" {r['command']}" if r["command"] else ""))
        print(f"log: {r['log']}")
    return 0


def _verify_base(repo, cfg, args) -> int:
    r = verify.verify_base(repo, cfg)
    print(r["result"] + (f" {r['command']}" if r["command"] else ""))
    print(f"log: {r['log']}")
    return 0
