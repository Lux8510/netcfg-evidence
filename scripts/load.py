"""Small HTTP load driver: percentiles, throughput and error rate.

Usage:
    python scripts/load.py --base http://127.0.0.1:8020 --requests 300 --concurrency 20 \
        --username 何深 --password netcfg-demo-1

Exit code is non-zero when any request fails or --max-p95 is exceeded.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx

CONFIG = "hostname load\ninterface eth1\n ip address 10.0.0.1/30\n!\n"


@dataclass
class Result:
    status: int
    duration_ms: float
    error: str | None = None


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def once(client: httpx.Client, base: str, index: int, device_id: str | None) -> Result:
    started = time.perf_counter()
    try:
        if index % 10 == 0 and device_id:
            response = client.post(base + f"/api/devices/{device_id}/configs",
                                   json={"text": CONFIG.replace("10.0.0.1/30", f"10.0.0.{index % 200 + 1}/30"),
                                         "source_name": f"load-{index}"})
        elif index % 5 == 0:
            response = client.get(base + "/api/health")
        else:
            response = client.get(base + "/api/devices")
        error = None if response.status_code < 400 else f"HTTP {response.status_code}"
        status = response.status_code
    except Exception as exc:  # noqa: BLE001 - report, do not crash the driver
        status, error = 0, type(exc).__name__
    return Result(status=status, duration_ms=(time.perf_counter() - started) * 1000, error=error)


def run(base: str, requests: int, concurrency: int, username: str, password: str) -> dict:
    with httpx.Client(timeout=20) as setup:
        login = setup.post(base + "/api/auth/login", json={"username": username, "password": password})
        if login.status_code != 200:
            raise SystemExit(f"login failed: {login.status_code} {login.text}")
        cookies = login.cookies
        device = setup.post(base + "/api/devices", json={"name": f"load-{int(time.time())}"}, cookies=cookies)
        device_id = device.json().get("data", {}).get("id") if device.status_code < 400 else None

    started = time.perf_counter()
    with httpx.Client(timeout=20, cookies=cookies) as client:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(lambda index: once(client, base, index, device_id), range(requests)))
    elapsed = time.perf_counter() - started

    durations = [item.duration_ms for item in results]
    errors = [item for item in results if item.error]
    return {
        "requests": len(results),
        "concurrency": concurrency,
        "elapsed_seconds": round(elapsed, 2),
        "throughput_rps": round(len(results) / elapsed, 1) if elapsed else 0.0,
        "errors": len(errors),
        "error_samples": [f"{item.status} {item.error}" for item in errors[:5]],
        "latency_ms": {
            "p50": round(percentile(durations, 0.50), 1),
            "p95": round(percentile(durations, 0.95), 1),
            "p99": round(percentile(durations, 0.99), 1),
            "max": round(max(durations) if durations else 0.0, 1),
            "mean": round(statistics.fmean(durations), 1) if durations else 0.0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8020")
    parser.add_argument("--requests", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--max-p95", type=float, default=0.0, help="0 disables the assertion")
    args = parser.parse_args()

    report = run(args.base, args.requests, args.concurrency, args.username, args.password)
    print(report)
    if report["errors"]:
        return 1
    if args.max_p95 and report["latency_ms"]["p95"] > args.max_p95:
        print(f"p95 {report['latency_ms']['p95']}ms exceeds {args.max_p95}ms")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
