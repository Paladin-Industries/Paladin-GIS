# -*- coding: utf-8 -*-
# Paladin GIS - ArcGIS Pro toolbox for wildfire tactic authoring and sync
# Copyright (C) 2026 Paladin Industries, Inc.
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details. You should have received a copy of the GNU General Public
# License along with this program; if not, see <https://www.gnu.org/licenses/>.
"""Paladin GIS toolbox for ArcGIS Pro.

Python-toolbox counterpart to the Paladin QGIS plugin. All wire-contract logic
lives in paladin_core, which is byte-identical in intent to the QGIS plugin's
config/tactics/s3_client — so a tactic drawn in Pro appears in QGIS and back.

Install: Catalog pane -> Toolboxes -> Add Toolbox -> this .pyt.
Keep paladin_core/ and paladin_pro/ beside this file.
"""
import os
import sys

import arcpy

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from paladin_core import geojson as core_geojson   # noqa: E402
from paladin_core import schema                    # noqa: E402
from paladin_pro import convert, settings as psettings   # noqa: E402
from paladin_pro import statefile, store as pstore, sync as psync  # noqa: E402


def _msg(text):
    arcpy.AddMessage(text)


def _add_tactics_to_map(gdb):
    """Put the tactic feature classes on the active map (never fatal)."""
    try:
        aprx = arcpy.mp.ArcGISProject("CURRENT")
        active = aprx.activeMap
        if active is None:
            return
        present = {lyr.name for lyr in active.listLayers()}
        for name in pstore.FC_BY_CATEGORY.values():
            path = os.path.join(gdb, name)
            if arcpy.Exists(path) and name not in present:
                active.addDataFromPath(path)
    except Exception as exc:
        arcpy.AddWarning("Could not add tactic layers to the map: %s" % exc)


class Toolbox(object):
    def __init__(self):
        self.label = "Paladin GIS"
        self.alias = "paladingis"
        self.tools = [ConfigureSettings, CreateTactic, ImportTactics,
                      SyncTactics, PullOnly]


# --------------------------------------------------------------------------- #
class ConfigureSettings(object):
    def __init__(self):
        self.label = "1) Configure Paladin Settings"
        self.description = (
            "Store your identity and credentials. Author must be your Paladin "
            "account email - it is what Paladin matches for visibility. NOTE: "
            "the Credentials section renders COLLAPSED; expand it to enter "
            "your keys.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        current = psettings.load_settings()

        def param(name, label, value=None, category=None,
                  datatype="GPString"):
            prm = arcpy.Parameter(displayName=label, name=name,
                                  datatype=datatype,
                                  parameterType="Optional", direction="Input")
            if value is not None:
                prm.value = value
            if category:
                prm.category = category
            return prm

        author = param("author", "Author (your Paladin account email)",
                       current.get("author"))
        org = param("org_id", "Organization ID", current.get("org_id"))
        visibility = param("visibility", "Default visibility for new tactics",
                           current.get("visibility"))
        visibility.filter.type = "ValueList"
        visibility.filter.list = schema.VISIBILITY_LEVELS

        access = param("access_key", "AWS Access Key ID",
                       current.get("access_key"), "Credentials")
        secret = param("secret_key", "AWS Secret Access Key",
                       current.get("secret_key"), "Credentials")
        token = param("session_token", "AWS Session Token (optional)",
                      current.get("session_token"), "Credentials")

        bucket = param("bucket", "S3 bucket", current.get("bucket"),
                       "Advanced")
        region = param("region", "AWS region", current.get("region"),
                       "Advanced")
        prefix = param("disturbance_prefix", "S3 key prefix",
                       current.get("disturbance_prefix"), "Advanced")
        return [author, org, visibility, access, secret, token,
                bucket, region, prefix]

    def execute(self, parameters, messages):
        values = {p.name: (p.valueAsText or "") for p in parameters}
        psettings.save_settings(values)
        _msg("Settings saved to %s" % psettings.settings_path())
        if not values.get("access_key") or not values.get("secret_key"):
            arcpy.AddWarning(
                "Access key and/or secret is blank - expand the Credentials "
                "category and enter them, or sync will fail.")


# --------------------------------------------------------------------------- #
class CreateTactic(object):
    def __init__(self):
        self.label = "2) Create Tactic From Features"
        self.description = (
            "Stamp features as Paladin tactics. Draw with Pro's normal editing "
            "tools on any scratch layer first, then run this on the result. "
            "Uses the current selection if there is one.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        features = arcpy.Parameter(
            displayName="Input features (uses selection if present)",
            name="in_features", datatype="GPFeatureLayer",
            parameterType="Required", direction="Input")

        tactic = arcpy.Parameter(
            displayName="Tactic type", name="tactic_type",
            datatype="GPString", parameterType="Required", direction="Input")
        tactic.filter.type = "ValueList"
        tactic.filter.list = sorted(schema.TACTIC_TYPES)

        label = arcpy.Parameter(displayName="Label", name="label",
                                datatype="GPString",
                                parameterType="Optional", direction="Input")
        notes = arcpy.Parameter(displayName="Notes", name="notes",
                                datatype="GPString",
                                parameterType="Optional", direction="Input")
        visibility = arcpy.Parameter(
            displayName="Visibility", name="visibility", datatype="GPString",
            parameterType="Optional", direction="Input")
        visibility.filter.type = "ValueList"
        visibility.filter.list = schema.VISIBILITY_LEVELS
        simulate = arcpy.Parameter(
            displayName="Include in simulation", name="simulate",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        simulate.value = schema.DEFAULT_SIMULATE
        eff_from = arcpy.Parameter(
            displayName="Effective from (UTC ISO)", name="effective_from",
            datatype="GPString", parameterType="Optional", direction="Input")
        eff_to = arcpy.Parameter(
            displayName="Effective to (UTC ISO)", name="effective_to",
            datatype="GPString", parameterType="Optional", direction="Input")
        return [features, tactic, label, notes, visibility, simulate,
                eff_from, eff_to]

    def updateMessages(self, parameters):
        """Block a geometry-type mismatch before it creates bad tactics."""
        tactic_type = parameters[1].valueAsText
        layer = parameters[0].value
        if not (tactic_type and layer):
            return
        info = schema.TACTIC_TYPES.get(tactic_type)
        if not info:
            return
        try:
            shape = arcpy.Describe(layer).shapeType
        except Exception:
            return
        expected = "Polygon" if info["category"] == "area" else "Polyline"
        if shape != expected:
            parameters[0].setErrorMessage(
                "%s expects %s geometry; this layer is %s."
                % (info["label"], expected, shape))

    def execute(self, parameters, messages):
        current = psettings.load_settings()
        layer = parameters[0].value
        tactic_type = parameters[1].valueAsText
        label = parameters[2].valueAsText or ""
        notes = parameters[3].valueAsText or ""
        visibility = parameters[4].valueAsText or current.get("visibility")
        simulate = bool(parameters[5].value)
        eff_from = parameters[6].valueAsText or ""
        eff_to = parameters[7].valueAsText or ""

        gdb = pstore.project_gdb()
        try:
            source_file = arcpy.Describe(layer).catalogPath
        except Exception:
            source_file = str(layer)

        made = 0
        with arcpy.da.SearchCursor(layer, ["SHAPE@"]) as cursor:
            for (geom,) in cursor:
                if geom is None:
                    continue
                name = "%s %d" % (label, made + 1) if (label and made) else label
                pstore.add_tactic(
                    gdb, tactic_type, geom, label=name, notes=notes,
                    org_id=current.get("org_id", ""),
                    author=current.get("author", ""), visibility=visibility,
                    simulate=simulate, effective_from=eff_from,
                    effective_to=eff_to, source="drawn",
                    source_file=source_file)
                made += 1

        _msg("Created %d %s tactic(s) in %s" % (made, tactic_type,
                                                os.path.basename(gdb)))
        _msg("Run '4) Sync Tactics' to upload.")
        _add_tactics_to_map(gdb)


# --------------------------------------------------------------------------- #
class ImportTactics(object):
    def __init__(self):
        self.label = "3) Import Tactics From File"
        self.description = ("Import a GeoJSON or shapefile of drawn features "
                            "as tactics of one type.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        source = arcpy.Parameter(
            displayName="File (GeoJSON / SHP)", name="in_file",
            datatype="DEFile", parameterType="Required", direction="Input")
        source.filter.list = ["geojson", "json", "shp"]
        tactic = arcpy.Parameter(
            displayName="Tactic type", name="tactic_type",
            datatype="GPString", parameterType="Required", direction="Input")
        tactic.filter.type = "ValueList"
        tactic.filter.list = sorted(schema.TACTIC_TYPES)
        return [source, tactic]

    def execute(self, parameters, messages):
        import json
        current = psettings.load_settings()
        path = parameters[0].valueAsText
        tactic_type = parameters[1].valueAsText
        gdb = pstore.project_gdb()
        made = 0

        if path.lower().endswith((".geojson", ".json")):
            with open(path, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            for feat in core_geojson.iter_features(obj):
                geom = convert.geojson_to_geometry(feat.get("geometry"))
                if geom is None:
                    continue
                pstore.add_tactic(
                    gdb, tactic_type, geom, org_id=current.get("org_id", ""),
                    author=current.get("author", ""), source="imported",
                    source_file=os.path.basename(path))
                made += 1
        else:
            with arcpy.da.SearchCursor(path, ["SHAPE@"]) as cursor:
                for (geom,) in cursor:
                    if geom is None:
                        continue
                    pstore.add_tactic(
                        gdb, tactic_type, geom,
                        org_id=current.get("org_id", ""),
                        author=current.get("author", ""), source="imported",
                        source_file=os.path.basename(path))
                    made += 1

        _msg("Imported %d feature(s) as %s" % (made, tactic_type))
        _add_tactics_to_map(gdb)


# --------------------------------------------------------------------------- #
class SyncTactics(object):
    def __init__(self):
        self.label = "4) Sync Tactics (upload + download)"
        self.description = (
            "Append-only sync with Paladin. Uploads new, edited and deleted "
            "tactics as immutable versions, then reads down the current "
            "version of everything visible to you. Local unsynced edits are "
            "never overwritten.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        return []

    def execute(self, parameters, messages):
        current = psettings.load_settings()
        uploaded, counts = psync.sync(current, _msg)
        _msg("Uploaded %d change(s)." % uploaded)
        summary = ", ".join("%s %d" % (k, v)
                            for k, v in sorted(counts.items()) if v)
        _msg("Download: %s" % (summary or "nothing new"))
        if counts.get("conflict"):
            arcpy.AddWarning(
                "%d tactic(s) have local unsynced edits that differ from "
                "upstream; your local copies were kept. Review them, then "
                "sync again." % counts["conflict"])
        _add_tactics_to_map(pstore.project_gdb())


# --------------------------------------------------------------------------- #
class PullOnly(object):
    def __init__(self):
        self.label = "5) Pull Tactics (download only)"
        self.description = ("Read down current tactics without uploading "
                            "anything.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        return []

    def execute(self, parameters, messages):
        current = psettings.load_settings()
        gdb = pstore.project_gdb()
        state = statefile.load_state(gdb)
        try:
            counts = psync.pull(gdb, current, state, _msg)
        finally:
            statefile.save_state(gdb, state)
        summary = ", ".join("%s %d" % (k, v)
                            for k, v in sorted(counts.items()) if v)
        _msg("Download: %s" % (summary or "nothing new"))
        _add_tactics_to_map(gdb)
