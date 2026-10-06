"""Linux/glibc diagnostic for folder-index allocation in the request thread pool.

Run inside the control-plane image, with DATABASE_URL set to an offline SQLite URL.
This is a synthetic allocation benchmark, not an API or production-load test.
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import json
import platform
import tempfile
import threading
import time
import tracemalloc
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def arena_count() -> int:
    libc = ctypes.CDLL(None)
    libc.fdopen.argtypes = [ctypes.c_int, ctypes.c_char_p]
    libc.fdopen.restype = ctypes.c_void_p
    libc.malloc_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
    libc.fflush.argtypes = [ctypes.c_void_p]
    libc.fclose.argtypes = [ctypes.c_void_p]
    import os

    with tempfile.TemporaryFile() as output:
        stream = libc.fdopen(os.dup(output.fileno()), b"w")
        if not stream:
            raise RuntimeError("fdopen failed")
        try:
            if libc.malloc_info(0, stream) != 0:
                raise RuntimeError("malloc_info failed")
            libc.fflush(stream)
            output.seek(0)
            return len(ET.fromstring(output.read()).findall("heap"))
        finally:
            libc.fclose(stream)


def measure(stage: str) -> dict:
    status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    current, peak = tracemalloc.get_traced_memory()
    result = {
        "stage": stage,
        "rss_mib": round(int(status["VmRSS"].split()[0]) / 1024, 2),
        "swap_mib": round(int(status["VmSwap"].split()[0]) / 1024, 2),
        "arenas": arena_count(),
        "traced_current_mib": round(current / 2**20, 2),
        "traced_peak_mib": round(peak / 2**20, 2),
    }
    print(json.dumps(result), flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--max-rss-mib", type=float, default=450)
    parser.add_argument("--max-arenas", type=int, default=2)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.libc_ver()[0] != "glibc":
        parser.error("run in the Linux/glibc control-plane image")
    if args.threads < 2 or args.iterations < 1:
        parser.error("threads must be >=2 and iterations >=1")

    # Include the normal application import footprint. Start tracing afterwards
    # so the retained temporary objects can be distinguished from startup state.
    import app.main  # noqa: F401

    tracemalloc.start()
    measure("cold_import")
    # A ~6.8 MB index with hundreds of inline documents, without real user data.
    payload = json.dumps(
        [
            {"path": f"{i}.md", "content": "x" * 8200, "mime": "text/markdown"}
            for i in range(826)
        ]
    )
    barrier = threading.Barrier(args.threads)

    def materialize(_: int) -> None:
        barrier.wait(timeout=30)
        for _ in range(args.iterations):
            parsed = json.loads(payload)
            body = json.dumps(parsed).encode()
            del parsed, body

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        list(pool.map(materialize, range(args.threads)))
        gc.collect()
        # Keep the workers alive, as the application's request pool does.
        result = measure("idle_after_index_workload")
    print(
        json.dumps(
            {
                "operations": args.threads * args.iterations,
                "elapsed_seconds": round(time.perf_counter() - started, 2),
            }
        ),
        flush=True,
    )
    if result["rss_mib"] + result["swap_mib"] >= args.max_rss_mib:
        print("FAIL: retained resident + swapped memory exceeds the budget")
        return 1
    if result["arenas"] > args.max_arenas:
        print("FAIL: allocator arena limit is absent")
        return 1
    print("PASS: allocator arena limit and idle memory budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
