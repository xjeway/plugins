"""Append-only event log (events.jsonl) and the metrics derived from it."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

from .paths import Repo
from .store import now_iso

REJECT_KINDS_COUNTED = ("conflict", "verify-failed", "review")


def record(repo: Repo, type_: str, req: Optional[dict] = None, **detail) -> None:
    event = {
        "ts": now_iso(),
        "type": type_,
        "requestId": req["id"] if req else None,
        "branch": req["branch"] if req else None,
        "issues": req.get("issues", []) if req else [],
        "detail": detail,
    }
    os.makedirs(repo.state_dir, exist_ok=True)
    with open(repo.path("events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def load(repo: Repo) -> List[dict]:
    p = repo.path("events.jsonl")
    if not os.path.exists(p):
        return []
    out: List[dict] = []
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue  # truncated/garbled line (e.g. crash mid-append)
            if isinstance(e, dict):
                out.append(e)
    return out


def _ratio(n: int, d: int) -> Optional[float]:
    return round(n / d, 3) if d else None


def metrics(events: List[dict]) -> Dict[str, object]:
    verified = {e["requestId"] for e in events if e["type"] == "verified"}
    conflicts = {e["requestId"] for e in events if e["type"] == "verified" and e["detail"].get("result") == "CONFLICT"}
    rejected = {
        e["requestId"] for e in events
        if e["type"] == "rejected" and e["detail"].get("kind") in REJECT_KINDS_COUNTED
    }
    human = sum(1 for e in events if e["type"] in ("held", "human-requested"))
    submitted_at = {e["requestId"]: e["ts"] for e in events if e["type"] == "submitted"}
    waits: List[float] = []
    merged = first_pass = 0
    bounced = set()
    for e in events:
        if e["type"] == "rejected":
            bounced.add(e["branch"])
        elif e["type"] == "merged":
            merged += 1
            if e["branch"] not in bounced:
                first_pass += 1
            bounced.discard(e["branch"])
            if e["requestId"] in submitted_at:
                start = datetime.fromisoformat(submitted_at[e["requestId"]])
                waits.append((datetime.fromisoformat(e["ts"]) - start).total_seconds())
    n = len(verified)
    return {
        "submitted": len(submitted_at),
        "verified": n,
        "merged": merged,
        "conflictRate": _ratio(len(conflicts), n),
        "rejectRate": _ratio(len(rejected), n),
        "humanInterventionRate": _ratio(human, n),
        "firstPassRate": _ratio(first_pass, merged),
        "avgQueueSeconds": round(sum(waits) / len(waits)) if waits else None,
    }
