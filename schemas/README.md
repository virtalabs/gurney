# Gurney schemas

This directory contains JSON Schemas for the YAML configuration files used by `gurney`.

## Files

- `topology.schema.json` — validates `topologies/<topology-id>/topology.yaml`
- `scenario.schema.json` — validates `topologies/<topology-id>/scenarios/<scenario-id>/scenario.yaml`
- `config.schema.json` — validates `topologies/<topology-id>/config.yaml`

All schemas use JSON Schema Draft-07.

## Running validation

From the project root:

```bash
uv run gurney validate
```

This command validates all discovered `topology.yaml`, `scenario.yaml`, and `config.yaml` files under `./topologies` against their respective schemas and exits non‑zero if any file is invalid.

