# small-clinic vs small-clinic-viper flow

## small-clinic (working flow)

**Command order:** register-webhook → emulate-tap-device → passive-discovery → orthanc-push-to-dicom-listener → get-assets → get-remote-assets

**Observed (run 2026-03-07):**

1. **register-webhook** – Blueflow returns `"Asset synchronization initiated"` and queues a connector task to POST to webhook.site.
2. **emulate-tap-device** – quick tap setup.
3. **passive-discovery** – starts detached (tapirx capturing traffic).
4. **orthanc-push-to-dicom-listener** – DICOM echo; traffic flows, tapirx upserts to Blueflow.
5. **get-assets** – Blueflow returns **count: 1** (ORTHANC, 192.168.10.6).
6. **get-remote-assets** – curls webhook.site requests; at least one POST has `totalCount: 1` and ORTHANC in `items`.

**Why it works:** The sync is triggered at register-webhook time but runs **asynchronously**. By the time the connector task runs, passive-discovery and orthanc-push have already run, so Blueflow has 1 asset and the webhook receives that payload.

---

## small-clinic-viper (current status)

**Command order (after fixes):** create-viper-api-key → emulate-tap-device → passive-discovery → orthanc-push (wait_s: 25) → register-custom-field-names → get-orthanc-mac → upsert-orthanc-asset → register-viper → register-webhook → get-assets (wait_s: 45) → check-connector-delivery → get-remote-assets.

### Verifying realistic AE Title (CR_ROOM1)

The topology sets `orthanc-sender` to `ORTHANC__DicomAet: "CR_ROOM1"`. TapirX captures that as the Calling AE Title and sends it to BlueFlow as `identifier`.

- **orthanc-sender log:** With the current topology, the log must show `DICOM server listening with AET CR_ROOM1 on port: 4242`. If it shows `AET ORTHANC`, the run used a stale compose — re-run so the scenario’s `.build/docker-compose.yaml` is regenerated from the topology.
- **BlueFlow asset `name`:** BlueFlow (closed-source) maps TapirX’s `identifier` to the asset; whether that appears as the API `name` / `display_name` depends on BlueFlow’s upsert logic. If the asset was **created** in this run (first time that MAC was seen), `name` should reflect the identifier (e.g. `CR_ROOM1`). If the asset was **updated** (same MAC from a previous run when the AET was still `ORTHANC`), BlueFlow may not overwrite `name`, so you may still see `ORTHANC`. For a clean check, tear down and run the scenario again so the asset is created with `identifier=CR_ROOM1` from the start.

**What we fixed:**

- Register webhook *after* discovery so Blueflow has ORTHANC before the first sync.
- Use the correct Viper endpoint: **`/api/v1/assets/integrationUpload`** (camelCase, no slash before `upload`), via the `viper_integration_upload_url` fact.
- Add a **get-orthanc-mac** step that curls `assets_url` and captures ORTHANC’s `mac_address` into a fact.
- Add an **upsert-orthanc-asset** step that POSTs to Blueflow’s `upsert_url` with a dummy CPE 2.3 string, role `"Imaging Modality"`, and the captured MAC, so the asset matches Viper’s `integrationUpload` contract and still upserts the existing ORTHANC asset.
  - Add a **register-custom-field-names** step that POSTs to Blueflow’s `/api/assetcustomfieldnames/` endpoint to register `cpe` and `role` as custom asset field names before we upsert ORTHANC.

Blueflow’s outgoing payload envelope (`items`, `page`, `pageSize`, `totalCount`, `totalPages`, `next`, `previous`) already matched Viper’s schema. The missing pieces were valid `cpe` and `role` values for the asset; the new upsert step ensures those fields are present before the connector syncs to Viper, and the custom field registration step validates the theory that Blueflow must first be told about these fields so it can store and expose them to the connector.

### Custom field names

The scenario now registers `cpe` and `role` as custom asset field names via `POST /api/assetcustomfieldnames/` (once per field) before fetching ORTHANC’s MAC and upserting the asset with those keys populated. This is purely a testbed change: if the Viper connector payload starts including non-empty `cpe` and `role` and the 400 from `/api/v1/assets/integrationUpload` disappears, it confirms that missing/empty required fields were the root cause; if not, the next step is to investigate how Blueflow expects per-asset custom field **values** to be set.

---

## small-clinic-webhook (webhook.site without Viper)

`small-clinic-webhook` mirrors the small-clinic flow but uses the richer Blueflow/Viper-style topology (Blueflow, tapirx, Orthanc, replay) and targets webhook.site directly instead of a Viper instance.

**Command order:** register-webhook → emulate-tap-device → passive-discovery → orthanc-push-to-dicom-listener → get-assets → get-remote-assets

The commands and behavior are effectively the same as `small-clinic`, but the scenario is defined in `topologies/blueflow-local/scenarios/small-clinic-webhook/scenario.yaml` and intended for validating the connector payload shape against a generic webhook endpoint.

---

## Recommended fix: register webhook after assets exist

Trigger the webhook (and Viper registration) **after** discovery has run and Blueflow has assets, so the first sync push includes ORTHANC.

**Reorder commands:**

1. create-viper-api-key (needed for register-viper)
2. emulate-tap-device
3. passive-discovery
4. orthanc-push-to-dicom-listener
5. **Short wait** (e.g. wait_s on a no-op or 10–30s) so tapirx can upsert
6. **register-webhook** (webhook_url = Viper integration upload URL)
7. **register-viper** (so Viper knows about Blueflow)
8. get-assets (optional short wait_s to allow connector to run)
9. get-remote-assets (Viper `/api/v1/assets` — should now include ORTHANC)

This keeps the “working” behavior: sync is initiated when Blueflow already has at least one asset, so the first push to Viper includes ORTHANC.

---

## Checking Blueflow connector logs

1. **Delivery status in scenario output**  
   The scenario includes a `check-connector-delivery` command that GETs the connector task URL returned by `register-webhook`. Its output shows delivery state (e.g. pending, success, failure) and any error details from Blueflow.

2. **Full container logs (verbose run)**  
   Run with `--verbose`. When the run finishes, gurney writes each service’s logs under:
   - `var/log/<scenario_name>/blueflow.log` – Django API (webhook registration, delivery creation).
   - `var/log/<scenario_name>/celery-worker.log` – Connector task (POST to Viper, retries, errors).

   Example: `var/log/small-clinic-viper/blueflow.log` and `var/log/small-clinic-viper/celery-worker.log`. These files are created only when `--verbose` is used.
