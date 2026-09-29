"""Hook commands, invoked by hooks/hooks.json with the hook payload on stdin."""
from __future__ import annotations

import sys

from . import hooks


def register(sub) -> None:
    for name, kind in (("hook-stop", "stop"), ("hook-prompt", "prompt")):
        p = sub.add_parser(name, help=f"{kind} hook (reads the hook payload from stdin)")
        p.set_defaults(func=_make(kind), needs_repo=False)


def _make(kind: str):
    def handler(args) -> int:
        try:
            stdin_text = sys.stdin.read()
        except Exception:
            stdin_text = ""
        code, out, err = hooks.run(kind, stdin_text)
        try:
            if out:
                print(out)
            if err:
                print(err, file=sys.stderr)
        except Exception:
            return 0  # a hook must never fail the session
        return code
    return handler
