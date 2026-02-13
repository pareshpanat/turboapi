# Why TurboAPI

TurboAPI is focused on a narrow goal: predictable ASGI performance with a small, readable codebase and practical production features.

## What it optimizes for
- Minimal dependencies (stdlib-first core)
- Predictable route/dependency execution model
- Security/OpenAPI/runtime features without framework bloat
- Easy extension points (middleware/hooks/schema transform)

## Benchmark methodology (reproducible)

Use this baseline process to compare TurboAPI with peers:

1. Environment
- Pin CPU model, memory, OS, Python version
- Disable noisy background processes
- Run locally or on isolated runner

2. Workloads
- Plain JSON route (`GET /ping`)
- Validation route (`POST /model`)
- Dependency-injected route
- File/static route (optional)
- WebSocket echo roundtrip (optional)

3. Server settings
- Same ASGI server, same workers, same loop settings
- Warmup phase (for example 15s)
- Measure phase (for example 60s)

4. Tooling
- Use `wrk`, `bombardier`, or `hey`
- Capture latency percentiles (p50, p95, p99), throughput, errors

5. Reporting
- Publish exact command lines
- Publish app code used for each framework
- Publish raw outputs and summary table

## Example command template

```bash
wrk -t4 -c128 -d60s http://127.0.0.1:8000/ping
```

## Caveats
- Microbenchmarks do not represent full production workloads.
- Middleware stacks, payload size, serialization complexity, network path, and DB latency dominate many real systems.
- Fast paths can invert once auth, tracing, and downstream I/O are included.
- Always benchmark with your real routes and deployment topology.
