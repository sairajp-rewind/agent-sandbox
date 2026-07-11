# OpenClaw Density & Performance Tests (ClusterLoader2)

This directory is a placeholder for the Group 8 (density + throughput) ClusterLoader2 load-test integration, which is deferred to a follow-up PR.

## Planned Recipes

1. **`openclaw-density-test.yaml`**
   - **Purpose**: Creates `N` OpenClaw sandboxes concurrently, measures startup latency, and asserts that the gateway HTTP root responds.
   - **Parameters**:
     - `NUM_SANDBOXES` (default `10`, override via `--testoverrides=numSandboxes=100`)
     - `TUNING_SET` (default `Sequence`, alternatives `RandomizedLoad`, `Uniform5qps`)
     - `WARMPOOL_REPLICAS` (default `2`)

2. **`openclaw-throughput-test.yaml`**
   - **Purpose**: Sustained throughput: hits the chat endpoint at a target QPS through the router on a fixed pool of sandboxes, and measures p50/p95/p99 latency for `M` minutes.
   - **Parameters**:
     - `TARGET_QPS` (default `10`)
     - `DURATION_MINUTES` (default `5`)
     - `POOL_SIZE` (default `5`)

Both recipes will emit `junit.xml` under `clusterloader2/`.

## Reference Conventions

For execution and template shapes, see the shared platform load-test conventions in [dev/load-test/README.md](../../../../dev/load-test/README.md).