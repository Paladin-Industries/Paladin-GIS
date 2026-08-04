# Paladin GIS — ArcGIS Pro toolbox

Python toolbox counterpart to the QGIS plugin. Same store, same versioning, so
a tactic drawn in Pro shows up in QGIS and back.

Windows only — ArcGIS Pro has no macOS build.

## Layout

    PaladinGIS.pyt     the five geoprocessing tools
    paladin_core/      wire contract: schema, versioning, geojson, s3, sigv4
                       (pure stdlib — mirrors the QGIS plugin's config.py,
                       tactics.py and s3_client.py)
    paladin_pro/       arcpy-facing: settings, statefile, convert, store, sync

`PaladinGIS.pyt` must sit beside both folders.

## Tools

1. **Configure Paladin Settings** — author email, org, credentials. The
   Credentials category renders collapsed; expand it before saving.
2. **Create Tactic From Features** — stamps drawn features as tactics.
   Validates geometry type against the tactic (a polygon layer cannot become
   a dozer line).
3. **Import Tactics From File** — GeoJSON or shapefile.
4. **Sync Tactics** — upload + download.
5. **Pull Tactics** — download only.

## Where things live

    <project home>\PaladinGIS.gdb\Paladin_Tactics_Area    polygons
    <project home>\PaladinGIS.gdb\Paladin_Tactics_Line    lines
    <project home>\paladin_sync_state.json                sync state
    %APPDATA%\PaladinGIS\settings.json                    settings

Sync state persisting to disk is a small improvement over the QGIS plugin,
where it is session-scoped: edits made yesterday still diff correctly today.

## Drawing

Draw on an ordinary scratch feature class with Pro's editing tools, then run
tool 2 on the result. **Do not draw directly into the `Paladin_Tactics_*`
layers** — those are managed by the toolbox, and a hand-drawn row has no
tactic identity and will confuse sync.

Once a feature is a tactic you can freely edit its shape or attributes in
place; the next sync picks that up as a new version. To delete, set `status`
to `inactive` and sync — that writes a tombstone and drops the local row.

## Sync semantics

Append-only, identical to the QGIS client:

* create → v1, edit → v+1 superseding the previous, delete → v+1 tombstone
* key layout `{prefix}/{tactic_id}/{version:06d}-{version_id}.geojson`, so the
  lexically greatest key in a lineage folder is the current version
* pull filters by visibility (public / your org / your own) and never
  overwrites local unsynced edits — those are reported as conflicts

## Verified vs. needs a live check

The sync semantics are tested against a mock-arcpy harness: create, no-op on
unchanged, edit with supersedes, cross-client pull, org visibility filtering,
tombstone propagation, conflict protection, and the version/key helpers
(including legacy flat keys). The contract is also checked field-by-field
against the QGIS plugin's `config.py`.

What cannot be exercised without ArcGIS Pro installed, so check on first run:

* `geometry.__geo_interface__` output and `arcpy.AsShape(dict, False)` input
* `CreateFeatureclass` + `AddField`, and `da` cursor field ordering with `SHAPE@`
* the quoted `where_clause` against file-GDB text fields
* `arcpy.mp.ArcGISProject("CURRENT")` inside a GP tool (fails to a warning)

Symbology is not applied yet — colors exist in `paladin_core.schema.TACTIC_TYPES`
if you want to add a renderer.
