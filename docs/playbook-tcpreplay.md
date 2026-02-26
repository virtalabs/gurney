# tcpreplay playbook

tcpreplay is used to replay pcap files onto a network interface so a listener (e.g. TapirX) can observe the traffic. In gurney scenarios such as `tapirx-dicom-discovery`, a **replay** node runs tcpreplay and shares the listener’s network namespace (`span: tapirx`), so the listener sees the replayed packets on `eth0`.

## Replay image

The replay sidecar image is built from [docker/replay.Dockerfile](../docker/replay.Dockerfile): Alpine with `tcpreplay` and `curl`. The container entrypoint is `tcpreplay`.

## Scenario pattern

- The replay node declares which artifacts it can use via `nodes[].artifacts: [<artifact-id>]`.
- A scenario command runs tcpreplay with an artifact reference in argv: `{ artifact: <id> }`, which resolves to `/opt/artifacts/<filename>` inside the container.
- Typical command shape: `tcpreplay -i eth0 /opt/artifacts/<file>.pcap` (or the same via `{ artifact: id }` in scenario YAML).

## Useful flags

| Flag | Purpose |
|------|---------|
| `-i <interface>` | Interface to send on (e.g. `eth0`) |
| `-t` | Single burst mode (exit after one send) |
| `--loop=N` | Loop the pcap N times |
| `--mbps=RATE` | Replay at given Mbps |

Run `tcpreplay --help` in the replay container for more options.

## Shell access

From the scenario build dir (e.g. `topologies/blueflow-local/scenarios/tapirx-dicom-discovery/.build`):

```bash
docker compose run --rm --no-deps --entrypoint /bin/sh replay
```

Then run `tcpreplay` manually; pcaps are under `/opt/artifacts` (mounted from `var/artifacts` on the host).
