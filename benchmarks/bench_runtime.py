from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from turbo import GZipMiddleware, Turbo


async def _run_one_http(app, path="/ping"):
    sent = []
    scope = {"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": []}
    events = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive():
        if events:
            return events.pop(0)
        return {"type": "http.disconnect"}

    async def send(msg):
        sent.append(msg)

    await app(scope, receive, send)


async def _benchmark_case(app, *, path: str, warmup: int, iterations: int):
    for _ in range(warmup):
        await _run_one_http(app, path=path)
    samples = []
    for _ in range(5):
        started = time.perf_counter()
        for _ in range(iterations):
            await _run_one_http(app, path=path)
        elapsed = time.perf_counter() - started
        samples.append((elapsed * 1_000_000.0) / iterations)
    return {
        "mean_us": statistics.mean(samples),
        "median_us": statistics.median(samples),
        "p95_us": sorted(samples)[int(len(samples) * 0.95) - 1],
        "samples_us": samples,
    }


async def run_suite(warmup: int, iterations: int):
    app_base = Turbo()

    @app_base.get("/ping")
    async def ping():
        return {"ok": True}

    app_mw = Turbo()
    app_mw.use_asgi(GZipMiddleware(minimum_size=1_000_000))

    @app_mw.get("/ping")
    async def ping_mw():
        return {"ok": True}

    base = await _benchmark_case(app_base, path="/ping", warmup=warmup, iterations=iterations)
    mw = await _benchmark_case(app_mw, path="/ping", warmup=warmup, iterations=iterations)
    return {
        "meta": {"warmup": warmup, "iterations": iterations},
        "cases": {
            "http_plain_json": base,
            "http_with_asgi_middleware": mw,
        },
    }


def _apply_gate(report: dict, baseline: dict, tolerance: float):
    failures = []
    for case_name, current in report.get("cases", {}).items():
        max_allowed = baseline.get("cases", {}).get(case_name, {}).get("max_mean_us")
        if max_allowed is None:
            continue
        allowed = float(max_allowed) * float(tolerance)
        if float(current["mean_us"]) > allowed:
            failures.append((case_name, current["mean_us"], allowed))
    return failures


def main():
    parser = argparse.ArgumentParser(description="TurboAPI runtime benchmark suite")
    parser.add_argument("--warmup", type=int, default=300)
    parser.add_argument("--iterations", type=int, default=3000)
    parser.add_argument("--baseline", type=Path, default=None, help="Path to baseline JSON")
    parser.add_argument("--tolerance", type=float, default=1.2, help="Allowed multiplier over baseline")
    parser.add_argument("--output", type=Path, default=Path("benchmarks/latest.json"))
    parser.add_argument("--gate", action="store_true", help="Fail when regression gate is exceeded")
    args = parser.parse_args()

    report = asyncio.run(run_suite(args.warmup, args.iterations))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

    if args.gate and args.baseline is not None:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        failures = _apply_gate(report, baseline, args.tolerance)
        if failures:
            for case_name, got, allowed in failures:
                print(f"[perf-gate] {case_name}: mean_us={got:.2f} allowed={allowed:.2f}")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
