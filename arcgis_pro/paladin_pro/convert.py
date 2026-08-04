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
"""arcpy geometry <-> GeoJSON (EPSG:4326).

Uses two arcpy features:
  geometry.__geo_interface__  -> GeoJSON-style dict
  arcpy.AsShape(dict, False)  -> arcpy geometry from GeoJSON
Everything is reprojected to WGS84 before serialization, per the contract.
"""
import arcpy

from paladin_core import schema

WGS84 = arcpy.SpatialReference(schema.WGS84_EPSG)


def _to_wgs84(geom):
    try:
        sr = geom.spatialReference
        if sr is None or sr.factoryCode != schema.WGS84_EPSG:
            return geom.projectAs(WGS84)
    except Exception:
        try:
            return geom.projectAs(WGS84)
        except Exception:
            return geom
    return geom


def geometry_to_geojson(geom):
    if geom is None:
        return None
    return dict(_to_wgs84(geom).__geo_interface__)


def geojson_to_geometry(geom_json):
    if not geom_json:
        return None
    try:
        return arcpy.AsShape(geom_json, False)   # False = GeoJSON, not esri
    except Exception:
        return None


def wkt_for_hash(geom):
    """WKT in 4326 for content hashing.

    The hash only ever compares a feature against its OWN prior state on the
    same client, so precision differences between clients do not matter.
    """
    if geom is None:
        return ""
    try:
        return _to_wgs84(geom).WKT
    except Exception:
        return ""
