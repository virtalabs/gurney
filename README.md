# Gurney

Reproducible, self-contained test environment that exercises BlueFlow's passive (pcap/protocol observation) and active (Nessus/Tenable-backed) scanning pipelines.

## Requirements

- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Python ≥ 3.11
- Docker Engine with `docker compose` v2
- Linux x86-64 (Ubuntu 22.04+)

## Quick Start

```bash
make install        # uv sync --all-extras
make pull TOPOLOGY=blueflow-local   # build/pull/sync using topologies/<id>/config.yaml
uv run gurney list
uv run gurney --json list
uv run gurney list --json
uv run gurney pull blueflow-local
uv run gurney run <topology-id>/<scenario-id>
uv run gurney --json run <topology-id>/<scenario-id>
uv run gurney run <topology-id>/<scenario-id> --json
uv run gurney validate   # validate topology/scenario/config YAML against JSON Schemas
```

Or use the `gurney` CLI after install: `gurney list`, `gurney run <topology-id>/<scenario-id>`, `gurney --json run <topology-id>/<scenario-id>`.

## Concepts

- **Topology** — A definition of the full environment: networks (with CIDRs), nodes (containers, IPs, images, healthchecks, `depends_on`). Topologies live at `topologies/<topology-id>/topology.yaml`. Gurney validates references (e.g. IP in CIDR, no circular deps) and generates `docker-compose.yaml` from them.
- **Scenario** — A runnable slice of a topology. Scenarios are co-located under their topology at `topologies/<topology-id>/scenarios/<scenario-id>/scenario.yaml`. One topology can back many scenarios.
- **Scenario ref** — Canonical scenario identifier in list output: `<topology-id>/<scenario-id>`.

## Layout

```text
topologies/
  <topology-id>/
    topology.yaml
    config.yaml
    scenarios/
      <scenario-id>/
        scenario.yaml

schemas/
  topology.schema.json
  scenario.schema.json
  config.schema.json

var/
  artifacts/
  index/
  log/
```

### Artifacts

- Artifacts are defined in the topology (`artifacts`: id, kind, filename, url) and are fetched/cached under `var/artifacts` on the host.
- Each scenario grants artifacts to nodes via `nodes[].artifacts: [<artifact-id>]`. Inside containers they are mounted at `/opt/artifacts`; command argv can use `{ artifact: <id> }` to resolve to `/opt/artifacts/<filename>`.
- Reproducibility config is per-topology at `topologies/<topology-id>/config.yaml`. `make pull TOPOLOGY=<topology-id>` or `gurney pull <topology-id>` syncs and fetches artifacts. Use `make clean-artifacts` to remove cached artifacts when you want to force a clean re-fetch.

## Commands

- `gurney run <topology-id>/<scenario-id>` — Run a scenario by scenario ref
- `gurney list` — List available scenarios grouped by topology using a lazy index
- `gurney pull <topology-id|topology-id/scenario-id>` — Pull/build/verify reproducibility assets via selected topology `config.yaml`
- `gurney teardown` — Force-remove all gurney-managed Docker resources
- Root `--json` flag switches any command to machine-readable NDJSON events:
  - `gurney --json run <topology-id>/<scenario-id>`
  - `gurney --json list`
  - `gurney --json pull <topology-id|topology-id/scenario-id>`
  - `gurney --json teardown`
- Appended `--json` is also supported and equivalent:
  - `gurney run <topology-id>/<scenario-id> --json`
  - `gurney list --json`
  - `gurney pull <topology-id|topology-id/scenario-id> --json`
  - `gurney teardown --json`
- Use only one `--json` flag per command invocation.

### NDJSON contract

- One compact JSON object per line (newline-delimited JSON).
- Common envelope keys: `event`, `command`, `ts`, optional `payload`.
- Each command emits a terminal event:
  - Success: `{"event":"completed","command":"<cmd>",...}`
  - Failure: `{"event":"failed","command":"<cmd>","payload":{"error":"..."}}`

## Scenarios

Scenarios live at `topologies/<topology-id>/scenarios/<scenario-id>/scenario.yaml`. Each scenario references its topology and can list `nodes` for a subset run. **Commands** use `run.argv` as the full command (binary + arguments); argv can use artifact refs (`{ artifact: <id> }`) and facts. Gurney overrides the image entrypoint for one-off runs so that argv is executed as the main process. **Checks** (http_check commands) are a list of URLs to GET; gurney runs a one-off curl container per URL and fails the scenario if any request fails. Optional **environment** is a per-node map of env var overrides; keys must be topology node names.

`gurney list` uses a lazy index under `var/index/` and regenerates it when `topologies/**/topology.yaml` or `topologies/**/scenarios/**/scenario.yaml` changes.

See [TapirX playbook](docs/playbook-tapirx.md) and [tcpreplay playbook](docs/playbook-tcpreplay.md) for usage and scenarios.

## Reproducible teardown validation

To validate that scenario runs are deterministic and leave no orphaned resources:

```bash
./scripts/validate-reproducible-teardown.sh
```

Runs the smoke-minimal scenario twice (or pass a scenario ref, e.g. `blueflow-local/smoke-docker`), diffs the output after normalizing timestamps/durations, and verifies no gurney-managed containers or networks remain.
