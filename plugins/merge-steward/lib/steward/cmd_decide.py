"""Steward commands: merge, reject, hold, approve, deny, requeue, log-event."""
from __future__ import annotations

from . import decide
from .errors import StewardError


def register(sub) -> None:
    p = sub.add_parser("merge", help="commit the verified merge into integration")
    p.add_argument("id")
    p.set_defaults(func=_merge)

    p = sub.add_parser("reject", help="bounce the request back to its worker")
    p.add_argument("id")
    p.add_argument("--kind", required=True, choices=decide.REJECT_KINDS)
    p.add_argument("--reason-file", required=True)
    p.set_defaults(func=_reject)

    p = sub.add_parser("hold", help="park a risky request until a human approves it")
    p.add_argument("id")
    p.add_argument("--note", required=True)
    p.set_defaults(func=_hold)

    p = sub.add_parser("approve", help="approve a held request (any session may run this)")
    p.add_argument("ref", help="request id or branch name")
    p.set_defaults(func=_approve)

    p = sub.add_parser("deny", help="deny a held request (any session may run this)")
    p.add_argument("ref", help="request id or branch name")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=_deny)

    p = sub.add_parser("requeue", help="put the request being processed back at the front of the queue")
    p.add_argument("id")
    p.set_defaults(func=_requeue)

    p = sub.add_parser("log-event", help="record a coordination or human-request event")
    p.add_argument("type", choices=("coordinated", "human-requested"))
    p.add_argument("--id")
    p.add_argument("--text", required=True)
    p.set_defaults(func=_log_event)


def _merge(repo, cfg, args) -> int:
    req = decide.merge(repo, cfg, args.id)
    print(f"merged {req['id']} as {req['mergeCommit'][:9]}")
    if req["promoteDue"]:
        print("promote due: run `steward promote`")
    return 0


def _reject(repo, cfg, args) -> int:
    try:
        with open(args.reason_file, encoding="utf-8") as f:
            reason = f.read()
    except OSError as e:
        raise StewardError(f"cannot read --reason-file {args.reason_file}: {e.strerror or e}") from None
    req = decide.reject(repo, cfg, args.id, args.kind, reason)
    print(f"rejected {req['id']} ({args.kind}); notice written for {req['branch']}")
    return 0


def _hold(repo, cfg, args) -> int:
    req = decide.hold(repo, cfg, args.id, args.note)
    print(f"held {req['id']} for approval")
    return 0


def _approve(repo, cfg, args) -> int:
    req = decide.approve(repo, args.ref)
    print(f"approved {req['id']} ({req['branch']}); it is back in the queue")
    return 0


def _deny(repo, cfg, args) -> int:
    req = decide.deny(repo, args.ref, args.reason)
    print(f"denied {req['id']} ({req['branch']})")
    return 0


def _requeue(repo, cfg, args) -> int:
    req = decide.requeue(repo, cfg, args.id)
    print(f"requeued {req['id']}")
    return 0


def _log_event(repo, cfg, args) -> int:
    decide.log_event(repo, args.type, args.id, args.text)
    print(f"recorded {args.type}")
    return 0
