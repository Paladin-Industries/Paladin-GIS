# Paladin for ArcGIS Pro

## Install (one time, ~30 seconds)

1. Save `PaladinGIS.pyt` anywhere permanent (e.g. `Documents\Paladin`).
2. In ArcGIS Pro: **Catalog pane → Toolboxes → right-click → Add Toolbox** → pick `PaladinGIS.pyt`.
3. Right-click the **Paladin** toolbox → **Add To Favorites**, then right-click it under Favorites → **Add To New Projects**. It now appears in every project.

No Python packages, no admin rights, no add-in. Requires ArcGIS Pro 3.x.

## Use

1. **Sign In** — email + password, once per computer. The tactic layers appear in your map.
2. **Draw** — Edit tab → **Create**. Under *Paladin Tactics (Lines)* / *(Areas)* there's a template per tactic (Dozer Line, Handline, Prescribed Burn…). Draw with any Pro tool (trace, freehand, snapping). Edit attributes (Name, Visibility, Include in Simulations, Line Width) in the Attributes pane; delete with Pro's normal Delete.
3. **Save edits** (Edit tab → Save).
4. **Sync with Paladin** — uploads your changes, downloads what's shared with you.

Also: **Import Tactics** (a map layer — respects selection — or a GeoJSON/KML/KMZ file) and **Add LANDFIRE Layers**.

Want a line-drawn fuel break or a polygon-drawn dozer line? Pick that template in the other layer — the geometry is whatever you draw; the tactic type is the template.

## How it maps to the QGIS plugin

Same wire contract (`paladin.tactic.v3`, Multi* geometries, all-string properties, `{prefix}/{tactic_id}/{version:06d}-{version_id}.geojson`), same mcleod broker and login. Data lives in `<project folder>\Paladin.gdb` (two feature classes with one subtype per tactic type + a sync-state table).

## Bugs fixed relative to QGIS 0.3.0 (also fixed in QGIS 0.3.1)

1. **Sync marked edits as synced before uploading.** A network failure mid-sync meant those edits were never sent. State is now committed per tactic after the upload succeeds.
2. **Version-number collisions.** Pushing an edit to a lineage someone else had bumped reused their version number → forked lineage, and mcleod's 409 was swallowed as "success" (edit silently lost). Remote is now listed first; next version = max(local, remote)+1, supersedes the actual current version; a real 409 leaves the tactic unsynced for the next run.
3. **Import put geometry in the wrong layer.** A polygon imported as "Dozer Line" was forced into the line layer (likely source of the polygon-shaped dozer lines WxPal has to coerce). Geometry kind now follows the data; points are skipped with a warning.
4. **Legacy flat keys** (`disturbances/{id}.geojson`) were re-downloaded every sync (lineage kept the `.geojson` suffix).
5. Tokens are DPAPI-encrypted at rest on Windows (QGIS stores them plaintext).

Pro-specific safety: deleting a synced feature publishes a tombstone, but if ≥3 tactics *and* more than half of everything synced vanish at once, Sync refuses unless *Advanced → Publish large deletions* is checked. Sync state lives inside `Paladin.gdb`, so deleting/replacing the gdb re-downloads instead of deleting everything upstream.

## Engineer first-run checklist (needs a live Pro session)

Sync logic is covered by `test/test_sync.py` against a mock arcpy (create/edit/delete/pull, concurrent edits, mid-sync failure, 409, mass-delete guard, imports incl. KMZ + EPSG:3857). Run: `cd test && python test_sync.py`. Not verifiable without Pro:

- [ ] Subtype feature classes show one Create Features template per tactic (if Pro adds them as subtype group layers, that's fine too).
- [ ] `style_layer` colors apply (best effort — failures are silent, default colors otherwise).
- [ ] `geom.__geo_interface__` / `arcpy.AsShape` round-trip; a curve drawn with the Arc tool syncs (densified).
- [ ] Running Sync with unsaved edits gives the "save your edits" message rather than a raw lock error.
- [ ] `addDataFromPath` for the LANDFIRE ImageServer URLs.
- [ ] Gate 1 of docs/VALIDATION.md passes against production mcleod.
