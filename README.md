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
make pull TOPOLOGY=blueflow-local   # build/pull/sync using topologies/<id>/config.yaml
uv run testbed list
uv run testbed pull blueflow-local
uv run testbed run <topology-id>/<scenario-id>
```

Or use the `testbed` script after install: `testbed list`, `testbed run <topology-id>/<scenario-id>`.

## Concepts

- **Topology** — A definition of the full environment: networks (with CIDRs), nodes (containers, IPs, images, healthchecks, `depends_on`). Topologies live at `topologies/<topology-id>/topology.yaml`. The testbed validates references (e.g. IP in CIDR, no circular deps) and generates `docker-compose.yaml` from them.
- **Scenario** — A runnable slice of a topology. Scenarios are co-located under their topology at `topologies/<topology-id>/scenarios/<scenario-id>/scenario.yaml`. One topology can back many scenarios.
- **Scenario ref** — Canonical scenario identifier in list output: `<topology-id>/<scenario-id>`.

## Layout

```text
topologies/
  <topology-id>/
    topology.yaml
    config.yaml
    fixtures/
      pcap/
    scenarios/
      <scenario-id>/
        scenario.yaml

var/
  artifacts/
    pcap/
  index/
  log/
```

### Artifact policy (hybrid fixtures + cache)

- **Committed fixtures:** keep small/stable topology fixtures in `topologies/<topology-id>/fixtures/pcap/`.
- **Persistent runtime cache:** fetched or large artifacts live in `var/artifacts/pcap/` and are kept across runs.
- Reproducibility config is per-topology at `topologies/<topology-id>/config.yaml` (lowercase for consistent naming).
- `make pull TOPOLOGY=<topology-id>` or `testbed pull <topology-id>` syncs topology fixtures into `var/artifacts/pcap/` and fetches any missing configured artifacts.
- `testbed run` expects replay pcaps at `/pcap/<filename>` inside containers via host mount `var/artifacts/pcap`.
- Use `make clean-artifacts` to remove cached artifacts when you want to force a clean re-fetch.

## Commands

- `testbed run <topology-id>/<scenario-id>` — Run a scenario by scenario ref
- `testbed list` — List available scenarios grouped by topology using a lazy index
- `testbed pull <topology-id|topology-id/scenario-id>` — Pull/build/verify reproducibility assets via selected topology `config.yaml`
- `testbed teardown` — Force-remove all testbed-managed Docker resources

## Scenarios

Scenarios live at `topologies/<topology-id>/scenarios/<scenario-id>/scenario.yaml`. Each scenario references its topology file and can optionally list `nodes` for a subset run. You can also add **commands**: each command's `run.argv` is the **full command** (binary + arguments), e.g. `tcpreplay -i eth0 /pcap/file.pcap` or `tapirx -iface eth0 -apiurl http://...`. The testbed overrides the image entrypoint for one-off runs so this argv is executed as the main process. **Checks** (http_check commands) are a special case: a list of URLs to GET. The testbed runs a one-off curl container for each URL; if any request fails (e.g. non-2xx or connection error), the scenario fails. Use this to verify that a service received expected data (e.g. `http://blueflow-api:8000/api/assets` to confirm tapirx-live sent assets to BlueFlow). Optional **environment** is a per-node map of env var overrides (e.g. `environment.blueflow-api.DEFAULT_USERNAME: admin`); keys must be topology node names, and values are merged over the topology’s node env at compose generation time.

`testbed list` uses a lazy index stored under `var/index/` and only regenerates it when either `topologies/**/topology.yaml` or `topologies/**/scenarios/**/scenario.yaml` changes.

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

To get a shell in the tapirx image: `docker run -it --rm --entrypoint /bin/sh tapirx:local`. To run with the scenario’s volumes and network: from `topologies/blueflow-local/scenarios/tapirx-dicom-discovery/.build`, run `docker compose run --rm --no-deps --entrypoint /bin/sh tapirx-pcap` (or `tapirx-live`), then run `tapirx` with the flags above (pcap is at `/pcap`, mounted from `var/artifacts/pcap`).

### Small clinic scenario

`blueflow-local/small-clinic` mirrors the same BlueFlow/TapirX check pattern as `tapirx-dicom-discovery`, but replaces pcap replay with Orthanc-originated network traffic directed at a Meddream node:

- `tapirx` runs in listener mode on `eth0` with `-verbose` enabled.
- `pacs-server` (Orthanc) is configured via REST with a `meddream` remote modality.
- Orthanc sends repeated C-ECHO requests to Meddream (`/modalities/meddream/echo`) to generate on-wire traffic observed by TapirX.
- A best-effort TAP emulation step (`tap0`) runs before capture.

Set `MEDDREAM_IMAGE` if you want to override the default Meddream image used by `blueflow-local/topology.yaml`.

## Reproducible teardown validation

To validate that scenario runs are deterministic and leave no orphaned resources:

```bash
./scripts/validate-reproducible-teardown.sh
```

Runs the smoke-minimal scenario twice (or pass a scenario ref, e.g. `blueflow-local/smoke-docker`), diffs the output after normalizing timestamps/durations, and verifies no testbed-managed containers or networks remain.
