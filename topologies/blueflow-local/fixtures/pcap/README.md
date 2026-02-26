# Pcap fixtures for blueflow-local

This directory contains source-owned pcap fixtures for the `blueflow-local` topology.

- Keep committed fixtures small and stable for onboarding and reproducible examples.
- Runtime-fetched or large artifacts should be cached in `var/artifacts/pcap`.
- Reproducibility settings for this topology are defined in `topologies/blueflow-local/config.yaml`.
- `testbed pull` syncs fixtures into the runtime cache used by scenario execution.
