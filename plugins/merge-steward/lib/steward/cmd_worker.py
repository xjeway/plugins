"""Worker commands: submit, inbox."""
from __future__ import annotations

import os

from . import inbox, lock
from .errors import StewardError
from .submit import submit


def register(sub) -> None:
    p = sub.add_parser("submit", help="rebase this branch onto integration and queue it for the steward")
    p.add_argument("--session-name", help="this session's name, so the steward can message it")
    p.add_argument("--session-id", default=os.environ.get("CLAUDE_SESSION_ID"))
    p.set_defaults(func=_submit)

    p = sub.add_parser("inbox", help="show (or acknowledge) the steward's rejection notice for this branch")
    p.add_argument("--ack", action="store_true")
    p.set_defaults(func=_inbox)


def _submit(repo, cfg, args) -> int:
    req = submit(repo, cfg, args.session_id, args.session_name)
    steward = lock.read(repo)
    print(f"submitted {req['id']} ({req['branch']} @ {req['head'][:9]})")
    if req.get("autoResolved"):
        print(f"auto-resolved hot files during the rebase: {', '.join(req['autoResolved'])}")
    if req.get("ackedNotice"):
        print(f"acknowledged the steward's notice about {req['ackedNotice']}")
    name = (steward.get("sessionName") or "(unknown)") if steward else "(no steward running yet)"
    print(f"steward: {name}")
    return 0


def _inbox(repo, cfg, args) -> int:
    branch = repo.branch()
    if branch is None:
        raise StewardError("HEAD is detached")
    notice = inbox.ack(repo, branch) if args.ack else inbox.pending(repo, branch)
    if notice is None:
        print("no pending notice")
        return 0
    print(("acknowledged: " if args.ack else "") + inbox.format_notice(notice))
    return 0
