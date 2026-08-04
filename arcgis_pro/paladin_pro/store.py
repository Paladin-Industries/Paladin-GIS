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
"""Tactic feature classes in a project-local file geodatabase.

The QGIS plugin keeps one memory layer per geometry kind; the Pro equivalent
is one feature class per kind inside <project home>/PaladinGIS.gdb:

    Paladin_Tactics_Area   (POLYGON)   fuel breaks, prescribed burns
    Paladin_Tactics_Line   (POLYLINE)  dozer / hand / scratch lines

Schema comes from paladin_core.schema.TACTIC_FIELDS, so the attribute contract
is identical across clients.
"""
import datetime
import os
import uuid

import arcpy

from paladin_core import schema, versioning
from . import convert

GDB_NAME = "PaladinGIS.gdb"
FC_BY_CATEGORY = {"area": "Paladin_Tactics_Area",
                  "line": "Paladin_Tactics_Line"}
GEOM_BY_CATEGORY = {"area": "POLYGON", "line": "POLYLINE"}


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def field_names():
    return list(schema.TACTIC_FIELD_NAMES)


def project_gdb():
    """Path to the project-local Paladin GDB, creating it if needed."""
    aprx = arcpy.mp.ArcGISProject("CURRENT")
    home = aprx.homeFolder or os.path.dirname(aprx.filePath)
    gdb = os.path.join(home, GDB_NAME)
    if not arcpy.Exists(gdb):
        arcpy.management.CreateFileGDB(home, GDB_NAME)
    return gdb


def ensure_fc(gdb, category):
    name = FC_BY_CATEGORY[category]
    path = os.path.join(gdb, name)
    if not arcpy.Exists(path):
        arcpy.management.CreateFeatureclass(
            gdb, name, GEOM_BY_CATEGORY[category],
            spatial_reference=convert.WGS84)
        for fname in schema.TACTIC_FIELD_NAMES:
            arcpy.management.AddField(path, fname, "TEXT", field_length=512)
    return path


def content_hash_row(geom, attrs):
    """Same hash the QGIS client computes: geometry WKT + change-relevant fields."""
    return versioning.content_hash(convert.wkt_for_hash(geom), attrs)


def _where_id(tactic_id):
    return "tactic_id = '%s'" % str(tactic_id).replace("'", "''")


def add_tactic(gdb, tactic_type, geom, *, label="", notes="", org_id="",
               author="", visibility=None, simulate=None, effective_from="",
               effective_to="", source="drawn", source_file=""):
    """Insert one tactic feature; returns its tactic_id."""
    info = schema.TACTIC_TYPES[tactic_type]
    fc = ensure_fc(gdb, info["category"])
    tactic_id = str(uuid.uuid4())
    attrs = {
        "tactic_id": tactic_id, "version_id": "", "version": "",
        "supersedes": "", "status": schema.STATUS_ACTIVE,
        "tactic_type": tactic_type, "model_role": info["model_role"],
        "label": label or info["label"], "notes": notes,
        "created_utc": utc_now_iso(),
        "effective_from_utc": effective_from, "effective_to_utc": effective_to,
        "org_id": org_id, "author": author,
        "visibility": visibility or schema.DEFAULT_VISIBILITY,
        "simulate": str(schema.DEFAULT_SIMULATE if simulate is None
                        else simulate),
        "source": source, "source_file": source_file,
    }
    names = field_names()
    with arcpy.da.InsertCursor(fc, ["SHAPE@"] + names) as cur:
        cur.insertRow([geom] + [attrs.get(n, "") for n in names])
    return tactic_id


def iter_tactics(gdb):
    """Yield (fc_path, attrs dict, geometry) for every tactic feature."""
    names = field_names()
    for category, fcname in FC_BY_CATEGORY.items():
        fc = os.path.join(gdb, fcname)
        if not arcpy.Exists(fc):
            continue
        with arcpy.da.SearchCursor(fc, ["SHAPE@"] + names) as cur:
            for row in cur:
                yield fc, dict(zip(names, row[1:])), row[0]


def update_fields(gdb, tactic_id, updates):
    names = field_names()
    for fcname in FC_BY_CATEGORY.values():
        fc = os.path.join(gdb, fcname)
        if not arcpy.Exists(fc):
            continue
        with arcpy.da.UpdateCursor(fc, names,
                                   where_clause=_where_id(tactic_id)) as cur:
            for row in cur:
                attrs = dict(zip(names, row))
                attrs.update(updates)
                cur.updateRow([attrs.get(n, "") for n in names])
                return True
    return False


def update_payload(gdb, tactic_id, props, geom):
    """Replace geometry + contract attributes from a downloaded payload."""
    names = field_names()
    for fcname in FC_BY_CATEGORY.values():
        fc = os.path.join(gdb, fcname)
        if not arcpy.Exists(fc):
            continue
        with arcpy.da.UpdateCursor(fc, ["SHAPE@"] + names,
                                   where_clause=_where_id(tactic_id)) as cur:
            for _row in cur:
                cur.updateRow([geom] +
                              [str(props.get(n, "")) for n in names])
                return True
    return False


def remove_tactic(gdb, tactic_id):
    for fcname in FC_BY_CATEGORY.values():
        fc = os.path.join(gdb, fcname)
        if not arcpy.Exists(fc):
            continue
        with arcpy.da.UpdateCursor(fc, ["tactic_id"],
                                   where_clause=_where_id(tactic_id)) as cur:
            for _row in cur:
                cur.deleteRow()
                return True
    return False


def add_from_payload(gdb, props, geom):
    """Insert a downloaded tactic, preserving its existing identity."""
    info = schema.TACTIC_TYPES.get(props.get("tactic_type"))
    if info is None:
        return False
    fc = ensure_fc(gdb, info["category"])
    names = field_names()
    with arcpy.da.InsertCursor(fc, ["SHAPE@"] + names) as cur:
        cur.insertRow([geom] + [str(props.get(n, "")) for n in names])
    return True


def find_tactic(gdb, tactic_id):
    for _fc, attrs, geom in iter_tactics(gdb):
        if attrs.get("tactic_id") == tactic_id:
            return attrs, geom
    return None
