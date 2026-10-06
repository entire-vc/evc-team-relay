# Control-plane memory diagnostics

The Python image uses glibc. Its default allocator can create multiple arenas
for the synchronous request pool. Decoding large inline folder indexes can leave
free memory in separate arenas after the request objects have been released.
This can raise RSS and swap usage without a corresponding increase in live
Python objects. The image sets `MALLOC_ARENA_MAX=2` at process startup to bound
that fragmentation. It does not change the container memory limit.

The setting is documented in the
[glibc allocator manual](https://sourceware.org/glibc/manual/latest/html_node/Memory-Allocation-Tunables.html).
Fewer arenas can introduce allocator contention; compare request latency and
memory on the actual workload after deploying. A real leak or too many
concurrent large requests still requires investigation.

Run the synthetic diagnostic from the repository root on a Linux Docker host
(use the host's native architecture for meaningful RSS measurements):

```sh
docker build -t control-plane-memory apps/control-plane
timeout 120 docker run --rm --network none \
  -e DATABASE_URL=sqlite+pysqlite:///:memory: \
  -v "$PWD/scripts/check_control_plane_memory.py:/probe.py:ro" \
  control-plane-memory python /probe.py
```

The script imports the application, measures cold RSS, then decodes and
serializes a synthetic 6.8 MB inline folder index 320 times across 16 persistent
worker threads. It reports idle RSS, swap, glibc arena count, live temporary
Python allocations, peak temporary allocations, and elapsed time. It fails if
idle RSS plus swap is at least 450 MiB or there are more than two arenas.
It uses no production data, network, database, or application startup hooks.
It is an allocator regression check, not a replacement for HTTP load testing.

Verify that the check catches an absent setting by running the same command
with `-e MALLOC_ARENA_MAX=0`; it must exit nonzero with the arena-limit failure.
Run the two cases sequentially to avoid combining their memory peaks.

For a running container, measure both resident and swapped memory, and compare
the request rate and route mix before and after rollout:

```sh
docker stats control-plane --no-stream
docker exec control-plane sh -c \
  'sed -n "/VmRSS/p;/VmSwap/p" /proc/1/status; cat /proc/1/smaps_rollup'
```

Use the deployment's actual container name. `docker stats` alone does not show
the full anonymous memory footprint when much of the process has been swapped.
To roll back the allocator change, redeploy the previous image; it contains the
previous startup environment as well as the previous application code.
