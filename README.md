# BlueFlow Testbed

Reproducible, self-contained test environment that exercises BlueFlow's passive (pcap/protocol observation) and active (Nessus/Tenable-backed) scanning pipelines.

## Requirements

- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Python ≥ 3.11
- Docker Engine with `docker compose` v2
- Linux x86-64 (Ubuntu 22.04+)

## Quick Start

```bash
make install        # uv sync --all-extras
uv run testbed list
uv run testbed run smoke-minimal
```

Or use the `testbed` script after install: `testbed list`, `testbed run smoke-minimal`.

## Concepts

- **Testbed (topology)** — A single definition of the full environment: networks (with CIDRs), nodes (containers, IPs, images, healthchecks, `depends_on`). Defined in a YAML file such as `testbed.yaml` at the project root. The testbed validates references (e.g. IP in CIDR, no circular deps) and generates `docker-compose.yaml` from it.
- **Scenario** — A runnable slice of a topology. Lives in `scenarios/<name>/scenario.yaml` and specifies: scenario name, which topology file to use, and optionally which nodes to run. If `nodes` is set, only those nodes and their transitive dependencies are started; if omitted, the full topology runs. One topology can thus back multiple scenarios (e.g. smoke-minimal vs smoke-docker).

## Commands

- `testbed run <scenario>` — Run a scenario (generate compose from topology, up, health checks, teardown)
- `testbed list` — List available scenarios (directories under `scenarios/` that contain `scenario.yaml`)
- `testbed teardown` — Force-remove all testbed-managed Docker resources

## Scenarios

Scenarios live in `scenarios/<name>/` with a `scenario.yaml` that names the scenario, references a topology file (e.g. `testbed.yaml`), and optionally lists `nodes` for a subset run.

## Reproducible teardown validation

To validate that scenario runs are deterministic and leave no orphaned resources:

```bash
./scripts/validate-reproducible-teardown.sh
```

Runs the smoke-minimal scenario twice (or pass a scenario name, e.g. smoke-docker), diffs the output after normalizing timestamps/durations, and verifies no testbed-managed containers or networks remain.
