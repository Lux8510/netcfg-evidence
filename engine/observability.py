"""Structured request logs and in-process counters.

Deliberately dependency-free: one JSON line per request, one JSON endpoint for
counters. Enough to answer "what happened" without adding Prometheus.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections import Counter
from typing import Any

_lock = threading.Lock()
_started = time.time()
_counters: Counter[str] = Counter()
_statuses: Counter[str] = Counter()
_paths: Counter[str] = Counter()
_duration_ms_total = 0.0
_slowest_ms = 0.0

request_logger = logging.getLogger("netcfg.request")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "logger": record.name,
                   "message": record.getMessage()}
        extra = getattr(record, "payload", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent: safe to call from tests and from the app factory."""
    root = logging.getLogger()
    if any(getattr(handler, "_netcfg", False) for handler in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._netcfg = True  # type: ignore[attr-defined]
    root.handlers = [handler]
    root.setLevel(level)


def record_request(*, request_id: str, method: str, path: str, status: int,
                   duration_ms: float, user: str | None) -> None:
    global _duration_ms_total, _slowest_ms
    with _lock:
        _counters["requests_total"] += 1
        if status >= 500:
            _counters["errors_5xx"] += 1
        elif status >= 400:
            _counters["errors_4xx"] += 1
        _statuses[str(status)] += 1
        _paths[f"{method} {path}"] += 1
        _duration_ms_total += duration_ms
        _slowest_ms = max(_slowest_ms, duration_ms)
    request_logger.info("request", extra={"payload": {
        "request_id": request_id, "method": method, "path": path, "status": status,
        "duration_ms": round(duration_ms, 1), "user": user or "-"}})


def record_model_call(*, provider: str, model: str, ok: bool, latency_ms: float,
                      usage: dict | None) -> None:
    with _lock:
        _counters["model_calls_total"] += 1
        if not ok:
            _counters["model_calls_failed"] += 1
        _counters[f"model_provider_{provider}"] += 1
        if isinstance(usage, dict):
            _counters["model_prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            _counters["model_completion_tokens"] += int(usage.get("completion_tokens") or 0)
        _counters["model_latency_ms_total"] += int(latency_ms)
    request_logger.info("model_call", extra={"payload": {
        "provider": provider, "model": model, "ok": ok,
        "latency_ms": round(latency_ms, 1),
        "prompt_tokens": (usage or {}).get("prompt_tokens"),
        "completion_tokens": (usage or {}).get("completion_tokens")}})


def snapshot() -> dict[str, Any]:
    with _lock:
        counters = dict(_counters)
        requests = counters.get("requests_total", 0)
        average = round(_duration_ms_total / requests, 1) if requests else 0.0
        return {
            "uptime_seconds": round(time.time() - _started, 1),
            "counters": counters,
            "status": dict(_statuses),
            "top_paths": _paths.most_common(10),
            "latency_ms": {"average": average, "slowest": round(_slowest_ms, 1)},
            "note": "进程内计数，重启清零；用于演示与排障，不是持久化指标存储",
        }


def reset() -> None:
    """Tests only."""
    global _duration_ms_total, _slowest_ms
    with _lock:
        _counters.clear()
        _statuses.clear()
        _paths.clear()
        _duration_ms_total = 0.0
        _slowest_ms = 0.0
