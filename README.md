# Paladin GIS

Desktop GIS clients for Paladin. Load LANDFIRE fuel and canopy layers, draw
wildfire tactics — fuel breaks, prescribed burns, dozer lines, handlines,
scratch lines — and sync them to Paladin's tactic store, where the fire model
composes them into the simulated fuelbed.

Two clients, one shared data contract:

| Client | Platform | Package |
|---|---|---|
| **QGIS plugin** | macOS, Windows, Linux | `paladin_gis-<version>.zip` |
| **ArcGIS Pro toolbox** | Windows only | `paladin_arcgis_pro-<version>.zip` |

Both write the same versioned GeoJSON to the same store, so a tactic drawn in
QGIS appears in Pro and vice versa.

> **Credentials required.** The packages are free to download and inspect, but
> syncing needs keys issued by Paladin. See [Getting access](#getting-access).

---

## Install — QGIS (macOS, Windows, Linux)

Requires **QGIS 3.28 or newer**. No third-party Python packages; the plugin
uses only the standard library and what ships with QGIS.

1. Download `paladin_gis-<version>.zip` from
   [Releases](../../releases/latest). **Do not unzip it.**
2. QGIS → **Plugins → Manage and Install Plugins… → Install from ZIP**.
3. Choose the file, click **Install Plugin**.
4. Open **Installed**, tick **Paladin GIS** if it isn't already.
5. A Paladin button appears on the toolbar and opens the panel.

<details>
<summary>Installing by hand instead</summary>

Unzip so that the `paladin_gis` folder lands in your QGIS profile's plugin
directory, then restart QGIS.

**macOS**
```
~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/
```

**Windows**
```
%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\
```

**Linux**
```
~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/
```

The result must be `…/python/plugins/paladin_gis/metadata.txt` — if you end up
with a nested `paladin_gis/paladin_gis/`, QGIS will not see it.
</details>

### macOS note

On first launch macOS may quarantine files downloaded through a browser. If
QGIS reports the plugin as broken immediately after install, clear the flag and
restart QGIS:

```bash
xattr -dr com.apple.quarantine \
  ~/Library/Application\ Support/QGIS/QGIS3/profiles/default/python/plugins/paladin_gis
```

---

## Install — ArcGIS Pro (Windows)

**ArcGIS Pro is Windows-only — there is no macOS build.** On a Mac, either use
the QGIS plugin, or run Pro in a Windows VM (Parallels/VMware) or on a remote
Windows workstation. Both clients talk to the same store, so mixing them across
a team is fine.

Requires **ArcGIS Pro 3.x**.

1. Download `paladin_arcgis_pro-<version>.zip` from
   [Releases](../../releases/latest).
2. Extract it somewhere **stable** — `C:\Paladin\arcgis_pro\` is a good choice.
   Not Downloads, not the Desktop: Pro stores the path to the toolbox, so
   moving the folder later breaks the reference.
3. Confirm the layout after extracting:
   ```
   C:\Paladin\arcgis_pro\
     PaladinGIS.pyt
     paladin_pro\
     paladin_core\
   ```
   `PaladinGIS.pyt` must sit beside both folders.
4. Open an ArcGIS Pro project. **Catalog pane → Toolboxes → right-click →
   Add Toolbox…** → select `PaladinGIS.pyt`.
5. Expand it; five numbered tools appear.

If the toolbox shows a red X or refuses to expand, that's a Python import
error — right-click it and choose **Check for errors** to see the message.

---

## Configure

Both clients need the same three things: your **author email**, your
**organization ID**, and the **access key / secret key** Paladin issued you.

The plugin talks to Paladin's object store directly — there is no API server
to point at, so the bucket and region defaults are correct as shipped unless
Paladin told you otherwise.

**QGIS** — open the panel, expand **Settings**, fill them in, save. Settings
live in your QGIS profile, not the project file, so they follow the
workstation rather than a project you share.

**ArcGIS Pro** — run tool **1) Configure Paladin Settings**. The credential
fields sit under a **Credentials** category, which Pro renders *collapsed* —
click the header to expand it, or you'll save blank keys and the first sync
will fail with "No AWS credentials". Settings are written to
`%APPDATA%\PaladinGIS\settings.json`.

The author email is what Paladin matches for visibility, so use the address on
your account. Full details in [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

---

## First run

**QGIS** — add a LANDFIRE layer from the panel, draw a tactic with the drawing
tools, then press Sync.

**ArcGIS Pro** — draw on an ordinary scratch feature class using Pro's editing
tools, then run **2) Create Tactic From Features** to stamp them as tactics,
then **4) Sync Tactics**. Don't draw directly into the `Paladin_Tactics_*`
layers; those are managed by the toolbox, and a hand-drawn row has no tactic
identity.

Ask Paladin for a test prefix for your first round-trip so nothing lands beside
production tactics while you're finding your footing.

---

## How sync behaves

Append-only. Creating a tactic writes version 1; editing writes v+1 superseding
the previous; deleting writes a tombstone. Nothing is ever overwritten, so a
tactic's history stays auditable. Pulling brings down the current version of
everything visible to you — public, your organization's, and your own — and
never clobbers local edits you haven't synced yet; those are reported as
conflicts for you to resolve.

Visibility is set per tactic: `public`, `org`, or `private`.

---

## Storage layout

Tactics are stored per organization, derived from the Org ID you enter in
settings:

    disturbances/orgs/<org_id>/<tactic_id>/<version>-<version_id>.geojson

There is no separate prefix to configure. Your credentials are scoped to that
path, so an incorrect Org ID surfaces as an S3 403 on the first sync rather
than as silently missing data.

## Getting access

Email **<sales@paladinindustries.com>** with your organization and the email
address you want tactics attributed to. Credentials can be rotated later
without disturbing anything already synced.

## Support

Bugs and feature requests: [open an issue](../../issues). Include your QGIS or
Pro version, your OS, and the plugin version from `metadata.txt` — and strip
any keys, emails, or organization IDs from tracebacks before posting.

For account or credential problems, email support instead of filing publicly.

## Building from source

```bash
git clone git@github.com:Paladin-Industries/Paladin-GIS.git
cd Paladin-GIS
./scripts/build_zip.sh     # writes dist/
```

Release process: [docs/RELEASING.md](docs/RELEASING.md).

## License

GPL-2.0-or-later — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

The code is free software. Access to the Paladin service is separate: reading
and writing tactics requires credentials issued under an active subscription.

## Documentation

- [Getting started](docs/GETTING_STARTED.md) — what the tools do, how they are
  built, install and first tactic. Written to be forwarded to customers.
- [Configuration reference](docs/CONFIGURATION.md)
- [Credentials sheet template](docs/CREDENTIALS_SHEET_TEMPLATE.md) — internal;
  fill in per customer and send separately from the getting-started guide.
- [Releasing](docs/RELEASING.md)
