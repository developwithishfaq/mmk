"""Request/response JSONL logging middleware."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


api_hit_log_lock = threading.Lock()


def append_api_log(path: str, entry: dict) -> None:
    line = json.dumps(entry, ensure_ascii=False, default=str)
    with api_hit_log_lock:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class ApiLoggingMiddleware(BaseHTTPMiddleware):
    """Log every API request + response body to a JSONL file."""

    def __init__(self, app, *, api_log_path: str, skip_path_prefixes: tuple[str, ...]) -> None:
        super().__init__(app)
        self._api_log_path = api_log_path
        self._skip = skip_path_prefixes

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        if any(path.startswith(p) for p in self._skip):
            return await call_next(request)

        start = time.monotonic()

        raw_body = await request.body()
        req_body: object = None
        if raw_body:
            try:
                req_body = json.loads(raw_body)
            except Exception:
                req_body = raw_body.decode("utf-8", errors="replace")

        async def _receive():
            return {"type": "http.request", "body": raw_body, "more_body": False}

        request = Request(request.scope, receive=_receive)

        response: Response = await call_next(request)
        elapsed_ms = round((time.monotonic() - start) * 1000)

        resp_chunks = []
        async for chunk in response.body_iterator:
            resp_chunks.append(chunk)
        resp_raw = b"".join(resp_chunks)

        resp_body: object = None
        if resp_raw:
            try:
                resp_body = json.loads(resp_raw)
            except Exception:
                resp_body = resp_raw.decode("utf-8", errors="replace")[:2000]

        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "method": request.method,
            "path": path,
            "query": str(request.url.query) or None,
            "status": response.status_code,
            "elapsed_ms": elapsed_ms,
            "req_body": req_body,
            "resp_body": resp_body,
        }

        append_api_log(self._api_log_path, entry)

        return Response(
            content=resp_raw,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
