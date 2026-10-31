"""Concurrency benchmark: how throughput and latency change as more requests run at once.

    openjev bench                                  every saved sample, concurrency 1, 2, 4, ... 32
    openjev bench --tag support --levels 1,4,16 --duration 30
    openjev bench --json results.json

The workload is your saved samples, sent round-robin. At each concurrency level, that many workers
send requests back to back for --duration seconds; the report gives completed requests and
questions per second, latency percentiles and errors per level.

By default every request gets a unique marker in its state, so a server's prefix cache can't
answer repeats of the same sample from memory (vLLM caches prompts). --reuse-cache sends the
samples unchanged, which measures repeated inputs instead.
"""
from __future__ import annotations

import itertools
import json
import threading
import time

from . import Jev, JevError

DEFAULT_LEVELS = (1, 2, 4, 8, 16, 32)


def _unique(state, n: int):
    """The sample's state with a marker that makes the prompt new to the server."""
    if isinstance(state, dict):
        return {"request": f"bench-{n}", **state}
    return f"[request bench-{n}]\n{state}"


def _pct(sorted_vals: list, p: float) -> float:
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, max(0, round(p / 100 * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def run_level(jev: Jev, workload: list, concurrency: int, duration: float, unique: bool, counter,
              stop: threading.Event = None, live: dict = None) -> dict:
    """`concurrency` workers send requests back to back until `duration` seconds have passed (or
    `stop` is set). `live`, if given, is updated as requests complete: {"done", "errors"}."""
    lat, questions, errors = [], [0], []
    lock = threading.Lock()
    deadline = time.perf_counter() + duration
    stop = stop or threading.Event()
    live = live if live is not None else {}
    live.update(concurrency=concurrency, done=0, errors=0, started=time.time(), duration=duration)

    def worker():
        while time.perf_counter() < deadline and not stop.is_set():
            n = next(counter)
            sample = workload[n % len(workload)]
            state = _unique(sample["state"], n) if unique else sample["state"]
            t0 = time.perf_counter()
            try:
                jev.ask(state, **sample["questions"])
                ms = (time.perf_counter() - t0) * 1000
                with lock:
                    lat.append(ms); questions[0] += len(sample["questions"]); live["done"] = len(lat)
            except (JevError, OSError) as e:
                with lock:
                    errors.append(str(e)[:120]); live["errors"] = len(errors)

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0   # includes requests still in flight at the deadline
    lat.sort()
    return {
        "concurrency": concurrency, "seconds": round(elapsed, 2),
        "requests": len(lat), "questions": questions[0], "errors": len(errors),
        "req_per_s": round(len(lat) / elapsed, 2), "q_per_s": round(questions[0] / elapsed, 2),
        "p50_ms": round(_pct(lat, 50)), "p95_ms": round(_pct(lat, 95)), "max_ms": round(lat[-1]) if lat else 0,
        "first_error": errors[0] if errors else "",
    }


def run(jev: Jev, workload: list, levels=DEFAULT_LEVELS, duration: float = 15.0, unique: bool = True,
        progress=None, stop_error_rate: float = 0.5, stop: threading.Event = None, live: dict = None) -> list:
    if not workload:
        raise ValueError("no samples to send")
    counter = itertools.count()
    # Warm-up: one request, so connection setup and lazy initialisation don't land in level 1.
    jev.ask(_unique(workload[0]["state"], -1) if unique else workload[0]["state"], **workload[0]["questions"])
    results = []
    for c in levels:
        if stop is not None and stop.is_set():
            break
        r = run_level(jev, workload, c, duration, unique, counter, stop, live)
        results.append(r)
        if progress:
            progress(r)
        total = r["requests"] + r["errors"]
        if total and r["errors"] / total > stop_error_rate:
            break   # the server is failing; higher levels would only fail more
    return results


def report(results: list, target: str = "", unique: bool = True) -> str:
    base = results[0]["req_per_s"] if results and results[0]["req_per_s"] else None
    peak = max((r["req_per_s"] for r in results), default=0) or 1
    lines = [f"OpenJev concurrency benchmark{' against ' + target if target else ''}"
             f" ({'unique inputs' if unique else 'repeated inputs, prefix cache allowed'})", "",
             f"{'conc':>4}  {'req/s':>6}  {'q/s':>6}  {'speedup':>7}  {'p50 ms':>7}  {'p95 ms':>7}  {'max ms':>7}  {'reqs':>5}  {'err':>3}  throughput"]
    for r in results:
        speed = f"{r['req_per_s'] / base:.1f}x" if base else "-"
        bar = "█" * max(1, round(28 * r["req_per_s"] / peak)) if r["req_per_s"] else ""
        lines.append(f"{r['concurrency']:>4}  {r['req_per_s']:>6.2f}  {r['q_per_s']:>6.2f}  {speed:>7}  {r['p50_ms']:>7}  "
                     f"{r['p95_ms']:>7}  {r['max_ms']:>7}  {r['requests']:>5}  {r['errors']:>3}  {bar}")
    best = max(results, key=lambda r: r["req_per_s"], default=None)
    if best and base:
        lines += ["", f"peak {best['req_per_s']:.2f} req/s ({best['q_per_s']:.2f} questions/s) at concurrency "
                      f"{best['concurrency']}, {best['req_per_s'] / base:.1f}x concurrency 1; p50 there {best['p50_ms']} ms"]
    errs = [r for r in results if r["errors"]]
    if errs:
        lines.append(f"errors at concurrency {', '.join(str(r['concurrency']) for r in errs)}; first: {errs[0]['first_error']}")
    return "\n".join(lines)


def save_json(path: str, results: list, target: str, unique: bool, duration: float, samples: int) -> None:
    with open(path, "w") as f:
        json.dump({"target": target, "unique_inputs": unique, "duration_s": duration, "samples": samples,
                   "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "levels": results}, f, indent=2)
