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
"""GeoJSON payload construction — the Paladin property contract."""
from . import schema


def build_feature(tactic_id, geometry, attrs):
    """One GeoJSON Feature with the full property contract.

    `geometry` is a GeoJSON geometry dict already in EPSG:4326.
    """
    props = {"paladin_schema": schema.TACTIC_SCHEMA, "crs": schema.WGS84}
    for name in schema.TACTIC_FIELD_NAMES:
        val = attrs.get(name)
        props[name] = "" if val is None else str(val)
    return {"type": "Feature", "id": tactic_id,
            "geometry": geometry, "properties": props}


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def iter_features(obj):
    """Features from a FeatureCollection, a bare Feature, or a list."""
    if obj is None:
        return []
    if isinstance(obj, list):
        return obj
    kind = obj.get("type")
    if kind == "FeatureCollection":
        return obj.get("features") or []
    if kind == "Feature":
        return [obj]
    return []


def first_props(obj):
    feats = iter_features(obj)
    return (feats[0].get("properties") or {}) if feats else {}
