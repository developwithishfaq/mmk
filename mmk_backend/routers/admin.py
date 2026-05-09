"""
admin.py — Diagnostic and admin routes.

Endpoints
---------
GET    /api-log
DELETE /api-log
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter

from mmk_backend.middleware.api_logging import api_hit_log_lock
from mmk_backend.runtime_state import runtime

router = APIRouter()


@router.get("/api-log")
def get_api_log(n: int = 100, path: str = "", status: int = 0):
    """
    Return the last *n* API log entries from api_hits.jsonl.

    Query params:
      n      — number of recent entries to return (default 100, max 5000)
      path   — filter by URL path substring
      status — filter by HTTP status code (0 = all)
    """
    n = min(max(n, 1), 5000)
    if not os.path.exists(runtime.settings.api_log):
        return {"count": 0, "entries": []}

    with api_hit_log_lock:
        with open(runtime.settings.api_log, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

    entries = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if path and path not in entry.get("path", ""):
            continue
        if status and entry.get("status") != status:
            continue
        entries.append(entry)
        if len(entries) >= n:
            break

    return {"count": len(entries), "entries": entries}


@router.delete("/api-log")
def clear_api_log():
    """Truncate the api_hits.jsonl file."""
    with api_hit_log_lock:
        with open(runtime.settings.api_log, "w", encoding="utf-8") as fh:
            fh.write("")
    return {"status": "cleared"}
