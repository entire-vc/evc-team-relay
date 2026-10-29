"""Load-test the control plane against a PATCH /shares storm and record a timeline.

Phases (each optional):
  1. baseline: ROUNDS x REQUESTS mixed reads (share list, share, files-index,
     relay token), CONCURRENCY at a time;
  2. storm: STORM folder-items-only PATCHes on one published folder share,
     STORM_CONCURRENCY at a time, each with a slightly different tree;
  3. cool-down: COOLDOWN seconds with no load, so the pool and memory can
     settle back to their baseline.

A sampler runs throughout, once per second: /v1/health and /metrics latency,
process_resident_memory_bytes, db_connections_{active,idle,overflow},
db_pool_timeouts_total. The timeline goes to --out (CSV); the summary (status
counts, max probe latency, RSS start/peak/end and the slope of the last third)
is printed as JSON. A flat last-third RSS slope is the plateau the run must show.

Idle-in-transaction is database state; read it separately while the run is in
progress, e.g.
  SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction';

Usage:
  python3 scripts/load_share_patch_storm.py --base-url http://localhost:8000 \
      --token "$ACCESS_TOKEN" --share-id <published-folder-share-uuid> \
      --rounds 24 --requests 231 --storm 2000 --cooldown 120 --out timeline.csv

Only stdlib + httpx. Never point it at a share with real user content: the
storm rewrites that share's web_folder_items.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import statistics
import subprocess
import time
from collections import Counter

import httpx

METRIC_NAMES = (
    "process_resident_memory_bytes",
    "db_connections_active",
    "db_connections_idle",
    "db_connections_overflow",
    "db_pool_timeouts_total",
)
METRIC_RE = re.compile(r"^(?P<name>[a-z_]+)(?:\{[^}]*\})? (?P<value>[-+0-9.eE]+)$")


def parse_metrics(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for line in text.splitlines():
        match = METRIC_RE.match(line)
        if match and match["name"] in METRIC_NAMES:
            values[match["name"]] = float(match["value"])
    return values


def folder_items(i: int) -> list[dict]:
    return [{"path": f"load/{n}.md", "name": f"{n}.md", "type": "doc"} for n in range(5 + i % 7)]


class Run:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.headers = {"Authorization": f"Bearer {args.token}"}
        self.statuses: dict[str, Counter] = {"baseline": Counter(), "storm": Counter()}
        self.samples: list[dict] = []
        self.phase = "start"
        self.started = time.monotonic()

    async def sample_forever(self, client: httpx.AsyncClient, stop: asyncio.Event) -> None:
        while not stop.is_set():
            row: dict = {"t": round(time.monotonic() - self.started, 1), "phase": self.phase}
            for probe, path in (("health_s", "/v1/health"), ("metrics_s", "/metrics")):
                t0 = time.perf_counter()
                try:
                    resp = await client.get(path, timeout=30)
                    row[probe] = round(time.perf_counter() - t0, 3)
                    if probe == "metrics_s" and resp.status_code == 200:
                        row.update(parse_metrics(resp.text))
                except httpx.HTTPError:
                    row[probe] = None
            if self.args.pid and "process_resident_memory_bytes" not in row:
                # process_* metrics exist only on Linux; elsewhere read RSS via ps.
                rss_kib = subprocess.run(
                    ["ps", "-o", "rss=", "-p", str(self.args.pid)],
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                if rss_kib:
                    row["process_resident_memory_bytes"] = float(rss_kib) * 1024
            self.samples.append(row)
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except TimeoutError:
                pass

    async def _bounded(self, phase: str, coros, concurrency: int) -> None:
        semaphore = asyncio.Semaphore(concurrency)

        async def one(coro_factory):
            async with semaphore:
                try:
                    resp = await coro_factory()
                    self.statuses[phase][resp.status_code] += 1
                except httpx.HTTPError as exc:
                    self.statuses[phase][type(exc).__name__] += 1

        await asyncio.gather(*(one(c) for c in coros))

    async def baseline(self, client: httpx.AsyncClient) -> None:
        share = self.args.share_id
        reads = [
            lambda: client.get("/v1/shares", headers=self.headers),
            lambda: client.get(f"/v1/shares/{share}", headers=self.headers),
            lambda: client.get(f"/v1/shares/{share}/files-index", headers=self.headers),
            lambda: client.post(
                "/v1/tokens/relay",
                json={"share_id": share, "doc_id": share, "mode": "read"},
                headers=self.headers,
            ),
        ]
        for _ in range(self.args.rounds):
            batch = [reads[i % len(reads)] for i in range(self.args.requests)]
            await self._bounded("baseline", batch, self.args.concurrency)

    async def storm(self, client: httpx.AsyncClient) -> None:
        def patch(i: int):
            return lambda: client.patch(
                f"/v1/shares/{self.args.share_id}",
                json={"web_folder_items": folder_items(i)},
                headers=self.headers,
            )

        await self._bounded(
            "storm", [patch(i) for i in range(self.args.storm)], self.args.storm_concurrency
        )

    async def main(self) -> dict:
        async with httpx.AsyncClient(base_url=self.args.base_url, timeout=60) as client:
            stop = asyncio.Event()
            sampler = asyncio.create_task(self.sample_forever(client, stop))
            await asyncio.sleep(3)  # pre-load baseline samples
            self.phase = "baseline"
            await self.baseline(client)
            self.phase = "storm"
            await self.storm(client)
            self.phase = "cooldown"
            await asyncio.sleep(self.args.cooldown)
            stop.set()
            await sampler
        return self.summary()

    def summary(self) -> dict:
        def column(name: str, phase: str | None = None) -> list[float]:
            return [
                s[name]
                for s in self.samples
                if s.get(name) is not None and (phase is None or s["phase"] == phase)
            ]

        rss = column("process_resident_memory_bytes")
        tail = self.samples[-max(len(self.samples) // 3, 2) :]
        tail_rss = [
            (s["t"], s["process_resident_memory_bytes"])
            for s in tail
            if s.get("process_resident_memory_bytes") is not None
        ]
        slope = None
        if len(tail_rss) >= 2:
            slope = statistics.linear_regression(*zip(*tail_rss)).slope  # bytes/s
        cooldown_active = column("db_connections_active", "cooldown")
        return {
            "statuses": {k: dict(v) for k, v in self.statuses.items()},
            "health_max_s": max(column("health_s"), default=None),
            "metrics_max_s": max(column("metrics_s"), default=None),
            "probe_failures": sum(1 for s in self.samples if s.get("health_s") is None),
            "pool_timeouts_total_end": (column("db_pool_timeouts_total") or [None])[-1],
            "pool_active_end_of_cooldown": cooldown_active[-1] if cooldown_active else None,
            "rss_mib": {
                "start": round(rss[0] / 2**20, 1) if rss else None,
                "peak": round(max(rss) / 2**20, 1) if rss else None,
                "end": round(rss[-1] / 2**20, 1) if rss else None,
                "last_third_slope_kib_per_s": round(slope / 1024, 2) if slope is not None else None,
            },
            "samples": len(self.samples),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", required=True, help="access token of the share owner")
    parser.add_argument("--share-id", required=True, help="published FOLDER share (test data)")
    parser.add_argument("--rounds", type=int, default=24)
    parser.add_argument("--requests", type=int, default=231)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--storm", type=int, default=2000)
    parser.add_argument("--storm-concurrency", type=int, default=50)
    parser.add_argument("--cooldown", type=float, default=120.0)
    parser.add_argument("--out", default="share_patch_storm_timeline.csv")
    parser.add_argument("--pid", type=int, help="server PID for RSS when /metrics lacks it")
    args = parser.parse_args()

    run = Run(args)
    summary = asyncio.run(run.main())
    fields = ["t", "phase", "health_s", "metrics_s", *METRIC_NAMES]
    with open(args.out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(run.samples)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
