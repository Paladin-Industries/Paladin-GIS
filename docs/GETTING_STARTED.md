# Paladin GIS — Getting Started

Desktop GIS tools for authoring wildfire tactics and sharing them with your
organization through Paladin.

Two clients, one shared data contract. A tactic drawn in either appears in the
other, and both feed the same fire model.

| Client | Platform |
|---|---|
| QGIS plugin | macOS, Windows, Linux |
| ArcGIS Pro toolbox | Windows only — ArcGIS Pro has no macOS build |

Mixed teams are fine. If some of your staff are on Mac, they use QGIS and still
see everything the Pro users publish.

---

## 1. What the tools do

**Bring in the fuels picture.** Both clients add LANDFIRE 2025 CONUS layers —
surface fuel models (Scott & Burgan 40), canopy cover, canopy height, canopy
base height, canopy bulk density — streamed from the USGS LANDFIRE Product
Service. Nothing is downloaded to your machine beyond what you view.

**Draw tactics.** Five types, split by how the fire model treats them:

| Tactic | Geometry | Model role |
|---|---|---|
| Fuel Break | Polygon | fuel modification |
| Prescribed Burn | Polygon | fuel modification |
| Dozer Line | Line | control line |
| Handline | Line | control line |
| Scratch Line | Line | control line |

Area tactics are composed into the simulated fuelbed. Line tactics are treated
as candidate barriers.

**Attach the operational context.** Every tactic carries a label, notes, an
effective window (from/to), the organization that owns it, the author, a
visibility level, and a flag for whether the fire model should include it. That
last one is independent of visibility: a proposed tactic can be shared with
your team while excluded from simulation.

**Sync.** One button pushes your changes and pulls everyone else's.

---

## 2. Who sees what

Every tactic carries one of three visibility levels, chosen when you draw it:

| Visibility | Who can see it |
|---|---|
| `public` | Every Paladin user |
| `org` | Anyone whose organization ID matches yours |
| `private` | Only the author |

**Organization** is set once, in settings, from the ID on your license sheet.
Everyone at your agency uses the same one — that is what makes `org` tactics
shared rather than personal.

**Author** is your Paladin account email. It identifies who created each
version and is what `private` matches on, so it must be the address on your
account rather than a nickname or a shared mailbox.

The default for new tactics is `org`, which is usually what you want: visible
to your people, not to the wider platform.

---

## 3. How versioning works

The store is **append-only**. Nothing is ever overwritten or hard-deleted.

* Creating a tactic writes version 1.
* Editing it writes version 2, which records that it supersedes version 1.
* Deleting writes a final version marked inactive — a tombstone — and the
  tactic disappears from everyone's map on their next sync.

Every version stays in the store, so the decision history of an incident
remains auditable after the fact: who changed what, when, and what it looked
like before.

When you pull, you get the current version of everything visible to you. If you
have local edits you have not yet synced and someone else has published a newer
version of the same tactic, **your local work is kept** and the tactic is
reported as a conflict for you to resolve. The tool will not silently discard
your edits.

---

## 4. How it is put together

Worth knowing if your IT group asks.

**No installed dependencies.** Both clients use only the Python standard
library plus what already ships with QGIS or ArcGIS Pro. There is nothing to
`pip install`, no vendored binaries, and no bundled third-party packages.

**Direct to object storage.** The clients sign their own requests to Paladin's
object store using AWS Signature Version 4, implemented in the standard
library. There is no Paladin API server in the path and no telemetry — the only
network destinations are the LANDFIRE service and the storage endpoint.

**Storage layout.** One object per version:

    {prefix}/{tactic_id}/{version}-{version_id}.geojson

The tactic ID is stable across every edit, so a folder is one tactic's full
history and the highest-numbered object is its current state.

**Payload.** Plain GeoJSON in EPSG:4326 with a documented property set, tagged
with a schema version the fire model checks before reading. Your data stays
readable with ordinary tools — there is no proprietary format holding it.

**Change detection.** The client hashes each tactic's geometry and the fields
that constitute a meaningful change. Re-syncing something you did not modify
uploads nothing.

**Local storage.** QGIS keeps tactics in project memory layers. ArcGIS Pro
writes them to a project-local file geodatabase (`PaladinGIS.gdb`) with a
sidecar sync-state file, so Pro's change tracking survives restarts.

**Source available.** The clients are open source under GPL-2.0-or-later:
<https://github.com/Paladin-Industries/Paladin-GIS>. Your security team can
read exactly what they do.

---

## 5. Installing

### QGIS (macOS, Windows, Linux) — QGIS 3.28 or newer

1. Download `paladin_gis-<version>.zip` from the
   [releases page](https://github.com/Paladin-Industries/Paladin-GIS/releases/latest).
   **Do not unzip it.**
2. **Plugins → Manage and Install Plugins… → Install from ZIP**, choose the
   file, **Install Plugin**.
3. Under **Installed**, make sure **Paladin GIS** is ticked.
4. The Paladin button on the toolbar opens the panel.

On macOS, if QGIS reports the plugin as broken right after installing, macOS
has quarantined the download. Clear it and restart QGIS:

```bash
xattr -dr com.apple.quarantine \
  ~/Library/Application\ Support/QGIS/QGIS3/profiles/default/python/plugins/paladin_gis
```

### ArcGIS Pro (Windows) — Pro 3.x

1. Download `paladin_arcgis_pro-<version>.zip` from the same releases page.
2. Extract it somewhere permanent — `C:\Paladin\arcgis_pro\` works well. **Not
   Downloads or the Desktop:** Pro remembers the path, so moving the folder
   later breaks the toolbox.
3. Check the result looks like this:

   ```
   C:\Paladin\arcgis_pro\
     PaladinGIS.pyt
     paladin_pro\
     paladin_core\
   ```

4. In Pro: **Catalog pane → Toolboxes → right-click → Add Toolbox…** and
   select `PaladinGIS.pyt`.
5. Expand it — five numbered tools appear.

---

## 6. First-time setup

You will have received a license sheet with your organization ID and
credentials. Enter them once per workstation.

**QGIS** — open the panel, expand **Settings**, fill in author email,
organization ID, access key and secret key, then save. These live in your QGIS
profile rather than the project file, so they follow the workstation and are
not shared when you send someone a project.

**ArcGIS Pro** — run tool **1) Configure Paladin Settings**. The key fields are
inside a **Credentials** section that Pro draws **collapsed** — click the
header to expand it before saving, or the keys save blank and the first sync
fails with "No AWS credentials".

---

## 7. First tactic

**QGIS** — add a fuels layer from the panel, pick a tactic type, draw on the
map, then press Sync.

**ArcGIS Pro** — draw on an ordinary scratch feature class using Pro's normal
editing tools, then run **2) Create Tactic From Features** to stamp them as
tactics, then **4) Sync Tactics**.

One rule in Pro: **do not draw directly into the `Paladin_Tactics_*` layers.**
Those are managed by the toolbox. Draw on a scratch layer and stamp it — that
is what assigns the tactic its identity. Once stamped, you can reshape it or
edit its attributes in place freely; the next sync captures that as a new
version.

To delete a tactic, set its `status` to `inactive` and sync.

---

## 8. If something goes wrong

**"No AWS credentials"** — the keys are blank. In Pro this is nearly always the
collapsed Credentials section in tool 1.

**Tactics sync, but a colleague cannot see them** — check that their
organization ID matches yours exactly, and that the tactic is not `private`.

**Nothing downloads** — confirm the key prefix matches what is on your license
sheet. A test prefix will not show production tactics.

**Conflicts reported after a sync** — someone published a newer version of a
tactic you had also edited locally. Your version was kept. Review both and
re-sync.

Bugs and feature requests:
<https://github.com/Paladin-Industries/Paladin-GIS/issues> — please strip keys,
emails and organization IDs from anything you paste there.

Account and credential problems: contact Paladin directly rather than filing a
public issue.
