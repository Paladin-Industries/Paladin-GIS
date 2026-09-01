# Paladin GIS — QGIS plugin

Data-ingress front door for the Paladin fire-behavior platform. Pull LANDFIRE
image services, sketch tactics on top of them, import existing
GeoJSON/KML/KMZ, and push everything to Paladin's S3 fuels-modification store
as versioned, metadata-tagged GeoJSON that the fire model composes into the
simulated fuelbed.

## Install (dev)

1. Zip the `paladin_gis/` folder (or copy it) into your QGIS plugins dir:
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
   - Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
2. Restart QGIS (or Plugins → Manage → Reload) and enable **Paladin**.
3. Click the shield icon in the toolbar to open the dock.

Requires QGIS ≥ 4.0 (PyQt6). **No third-party dependencies** — the default
sync backend signs S3 requests with AWS SigV4 using only the Python standard
library, so there is nothing to pip-install.

## Workflow

1. **LANDFIRE image services** — the catalog is LANDFIRE LF2025 CONUS fuels
   (surface + canopy). Double-click a dataset (or select + *Add selected
   layer*) to load it as a raster under a `LANDFIRE` group.
2. **Draw tactic** — pick Fuel Break / Rx Burn / Scratch / Handline / Dozer /
   Hose Lay / Retardant, then choose **Polygon** or **Line** (Line records a
   footprint width in metres). Left-click vertices, right-click or double-click
   to finish. Tick *Freehand* to press-drag-release ("free polygon"). Notes +
   effective-time window are attached as metadata.
3. **Import file** — bring in GeoJSON/KML/KMZ, tagging what it represents.
4. **Tactics** — running list of your tactics AND everything synced down from
   S3 that you're allowed to see. Buttons: **Edit** (hands the selected tactic
   to QGIS's Vertex Tool to drag/stretch/add/remove points; click again to
   save), **Zoom**, **Delete** (soft-delete), **Refresh**. Editing a tactic
   makes the next Sync write a new version in its lineage.
5. **Sync to Paladin** — pushes each tactic as its own GeoJSON to S3, then
   reads down every disturbance under the shared prefix into read-only layers.

## Payload contract & versioning

One GeoJSON `FeatureCollection` per **version** (`schema = paladin.tactic.v3`),
EPSG:4326. Append-only: nothing is ever overwritten or hard-deleted.

Identity / audit properties: `tactic_id` (stable lineage), `version_id` (this
version), `version` (int), `supersedes` (previous `version_id`), `status`
(`active` | `inactive`), `created_utc` (this version's time), `author`.
Plus `tactic_type`, `model_role` (`control_line` | `fuel_modification`),
`label`, `notes`, `effective_from_utc`, `effective_to_utc`, `org_id`,
`visibility` (`public`|`org`|`private`), `simulate` (`"true"`|`"false"`),
`source`, `source_file`, `line_width_m`.

`line_width_m` is the real-world **footprint width in metres** for a LINE
feature (empty for polygons). Geometry kind is now chosen per feature at draw
time (the **Geometry** toggle: Polygon or Line) and is independent of the
tactic type — you can sketch a fuel break as a centerline + width instead of
tracing both edges. `model_role` still follows the *type*, not the drawn
geometry: a line's footprint is its centerline buffered by `line_width_m`, and
**the sim consumer must do that buffering** — the plugin only records the width.
The field is an additive, optional property, so `paladin_schema` stays
`paladin.tactic.v3` and older consumers keep accepting payloads (they ignore
it). Editing a line's width bumps a new version like any other change.

Key layout (folder per lineage):
- `{disturbance_prefix}/{tactic_id}/{version:06d}-{version_id}.geojson`

Operations on Sync:
- **create** -> version 1.
- **edit** (tactic changed since last sync, detected by content hash) -> a new
  version, `version+1`, `supersedes` = prior version, old object retained.
- **delete** -> Delete marks the tactic inactive; next Sync writes a tombstone
  version (`status: inactive`) carrying the last geometry, then drops it locally.

Read-down groups objects by lineage, takes the highest `version`, and shows it
only if `active` and visible — so the map reflects current state while the
bucket holds the entire decision trail. Legacy flat `{prefix}/{id}.geojson`
objects are treated as single-version lineages. The sim consumer filters on
`simulate` and keys geometry by `model_role`.

Read-down tactics are ingested into the editable store (not a separate
read-only layer), so they appear in the Tactics list and can be edited — an edit
continues that lineage (v+1, correct `supersedes`, your `author`). Reconcile on
Sync: a lineage new to you is **added**; if you hold an older version and have no
local edits it's **updated** to the current one; an upstream tombstone **removes**
it locally; if you have unsynced local edits they're **kept** (upstream ignored
for that lineage — no silent overwrite). After a QGIS restart the in-memory store
is empty; a Sync re-ingests everything with correct lineage, so cross-session
editing works. The one thing a restart loses is locally-drawn tactics you never
synced. A server-side lineage graph (the MongoDB backend) is still the real home
for concurrent-edit merging.

## Bucket, credentials, and settings

One shared bucket, one `disturbances/` folder. Bucket, region, prefix, backend (native SigV4, no install), session token, and API base/token are fixed defaults in `config.py` --
customers never configure them. Plugin Settings exposes only the four per-user
values: **Author** (individual, for private tactics), **Org id** (drives `org`
visibility), and the **AWS Access Key ID / Secret** you email them.

Access is honor-system for now: every customer key can read/write the whole
`disturbances/` folder, and the plugin honors each file's `visibility`/`org_id`
when reading down. It is not S3-enforced -- that lands with the token -> Paladin
API -> MongoDB backend. Mint one IAM user per customer (scoped to
`disturbances/*`) so you can revoke individually. See `aws_setup/` for the S3 +
IAM console walkthrough and the policy JSON.

## Known risks / decisions (read before shipping)

- **ImageServer loading.** LANDFIRE endpoints are ArcGIS *ImageServer*, loaded
  via the `arcgismapserver` provider. Modern QGIS handles this; on older builds
  it may hit the wrong export path. Fallbacks: enable WMS on the service, or use
  the WCS mirror at `edcintl.cr.usgs.gov`.
- **Endpoint drift.** Catalog is LF2025 CONUS. FBFM40 is confirmed live; the
  canopy layers follow the naming convention but weren't each pinged. Run
  `python -m paladin_gis.landfire_catalog` to check them all.
- **Credentials.** Default backend signs with SigV4 (stdlib, no boto3). The
  customer pastes the Access Key ID + Secret you email them; QgsSettings stores
  them in plaintext on disk, so mint one tightly-scoped IAM user per customer and
  revoke by deleting that key. The `presigned` backend (no keys on the client)
  is the token -> API -> MongoDB path.
- **Concurrency.** Per-tactic keys mean two editors never clobber one file, but
  there's no per-tactic locking or CRDT merge here — last writer to a given
  `tactic_id` wins. Fine for prefire setup; not the live sandbox.
- **Read-down scope.** `disturbances/` is listed with no auth scoping in this
  build; the Paladin API is where you enforce who can see whose data.
