# TapirX playbook

The `tapirx-dicom-discovery` scenario runs **two TapirX instances**: one reads DICOM from a pcap file; the other listens on an interface and receives the same pcap replayed by a **tcpreplay** sidecar. Both POST discovered assets to the mock API. See [tcpreplay playbook](playbook-tcpreplay.md) for replay usage.

## TapirX flags

| Flag | Purpose |
|------|---------|
| `-pcap <path>` | Read from a pcap file instead of live capture |
| `-apiurl <url>` | POST discovered assets to this URL (e.g. `/api/assets/upsert`) |
| `-verbose` | Show verbose output |
| `-limit <n>` | Exit after N packets (0 = unlimited) |
| `-sequential` | Process packets sequentially |
| `-iface <name>` | Interface for live capture (default `eth0`) |

## Pcap example

Inside the tapirx container or with artifacts mounted at `/opt/artifacts`:

```bash
tapirx -pcap /opt/artifacts/DICOM_C-ECHO-echoscu.pcap -verbose -limit 100 -sequential
```

With API upload:

```bash
tapirx -pcap /opt/artifacts/DICOM_C-ECHO-echoscu.pcap -apiurl http://mock-asset-api:8000/api/assets/upsert -limit 100 -sequential
```

## Listener example

Live capture on an interface:

```bash
tapirx -iface eth0 -verbose -limit 500
```

## Shell access

- **Standalone:** `docker run -it --rm --entrypoint /bin/sh tapirx:local`
- **With scenario network and mounts:** from `topologies/blueflow-local/scenarios/tapirx-dicom-discovery/.build`, run `docker compose run --rm --no-deps --entrypoint /bin/sh tapirx`, then run `tapirx` with the flags above. Artifacts are at `/opt/artifacts` (mounted from `var/artifacts` on the host).

## Small clinic scenario

`blueflow-local/small-clinic` mirrors the same BlueFlow/TapirX check pattern as `tapirx-dicom-discovery`, but replaces pcap replay with Orthanc-originated network traffic directed at a Meddream node:

- `tapirx` runs in listener mode on `eth0` with `-verbose` enabled.
- `pacs-server` (Orthanc) is configured via REST with a `meddream` remote modality.
- Orthanc sends repeated C-ECHO requests to Meddream (`/modalities/meddream/echo`) to generate on-wire traffic observed by TapirX.
- A best-effort TAP emulation step (`tap0`) runs before capture.

Set `MEDDREAM_IMAGE` if you want to override the default Meddream image used by `blueflow-local/topology.yaml`.
