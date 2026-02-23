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

Scenarios live in `scenarios/<name>/` with a `scenario.yaml` that names the scenario, references a topology file (e.g. `testbed.yaml`), and optionally lists `nodes` for a subset run. You can also add **commands**: each command's `run.argv` is the **full command** (binary + arguments), e.g. `tcpreplay -i eth0 /pcap/file.pcap` or `tapirx -iface eth0 -apiurl http://...`. The testbed overrides the image entrypoint for one-off runs so this argv is executed as the main process. **Checks** (http_check commands) are a special case: a list of URLs to GET. The testbed runs a one-off curl container for each URL; if any request fails (e.g. non-2xx or connection error), the scenario fails. Use this to verify that a service received expected data (e.g. `http://blueflow-api:8000/api/assets` to confirm tapirx-live sent assets to BlueFlow). Optional **environment** is a per-node map of env var overrides (e.g. `environment.blueflow-api.DEFAULT_USERNAME: admin`); keys must be topology node names, and values are merged over the topology’s node env at compose generation time.

## TapirX playbook

The `tapirx-dicom-discovery` scenario runs **two TapirX instances**: one reads DICOM from a pcap file; the other listens on an interface and receives the same pcap replayed by a **tcpreplay** sidecar. Both POST discovered assets to the mock API. Useful TapirX flags:

| Flag | Purpose |
|------|---------|
| `-pcap <path>` | Read from a pcap file instead of live capture |
| `-apiurl <url>` | POST discovered assets to this URL (e.g. `/api/assets/upsert`) |
| `-verbose` | Show verbose output |
| `-limit <n>` | Exit after N packets (0 = unlimited) |
| `-sequential` | Process packets sequentially |
| `-iface <name>` | Interface for live capture (default `eth0`) |

**Pcap example** (e.g. inside the tapirx container or with pcap mounted):

```bash
tapirx -pcap /pcap/DICOM_C-ECHO-echoscu.pcap -verbose -limit 100 -sequential
```

With API upload:

```bash
tapirx -pcap /pcap/DICOM_C-ECHO-echoscu.pcap -apiurl http://mock-asset-api:8000/api/assets/upsert -limit 100 -sequential
```

**Listener example** (live capture on an interface):

```bash
tapirx -iface eth0 -verbose -limit 500
```

To get a shell in the tapirx image: `docker run -it --rm --entrypoint /bin/sh tapirx:local`. To run with the scenario’s volumes and network: from `scenarios/tapirx-dicom-discovery/.build`, run `docker compose run --rm --no-deps --entrypoint /bin/sh tapirx-pcap` (or `tapirx-live`), then run `tapirx` with the flags above (pcap is at `/pcap`).

## Reproducible teardown validation

To validate that scenario runs are deterministic and leave no orphaned resources:

```bash
./scripts/validate-reproducible-teardown.sh
```

Runs the smoke-minimal scenario twice (or pass a scenario name, e.g. smoke-docker), diffs the output after normalizing timestamps/durations, and verifies no testbed-managed containers or networks remain.
