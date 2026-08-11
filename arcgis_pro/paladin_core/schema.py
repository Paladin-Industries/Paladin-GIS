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
"""Tactic schema — mirrors the QGIS plugin's config.py.

Kept free of any GIS import so the same contract can be used from arcpy,
QGIS, or plain Python.
"""

#: Bump when the GeoJSON property contract changes. The fire model reads
#: `paladin_schema` first and refuses payloads it does not understand.
TACTIC_SCHEMA = "paladin.tactic.v3"

#: CRS every payload is written in. Non-negotiable: the model expects lon/lat.
WGS84 = "EPSG:4326"
WGS84_EPSG = 4326

STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"

VISIBILITY_LEVELS = ["public", "org", "private"]
DEFAULT_VISIBILITY = "org"
DEFAULT_SIMULATE = True

#: category routes to the right feature class; model_role tells the fire model
#: how to treat the feature.
TACTIC_TYPES = {
    "fuel_break": {
        "label": "Fuel Break", "category": "area",
        "model_role": "fuel_modification", "color": (255, 176, 0, 90),
    },
    "prescribed_burn": {
        "label": "Prescribed Burn", "category": "area",
        "model_role": "fuel_modification", "color": (214, 69, 65, 90),
    },
    "scratch_line": {
        "label": "Scratch Line", "category": "line",
        "model_role": "control_line", "color": (255, 214, 0, 220),
    },
    "handline": {
        "label": "Handline", "category": "line",
        "model_role": "control_line", "color": (0, 200, 120, 220),
    },
    "dozer_line": {
        "label": "Dozer Line", "category": "line",
        "model_role": "control_line", "color": (120, 90, 60, 230),
    },
}

#: Order matters — attributes are written positionally in some clients.
TACTIC_FIELDS = [
    ("tactic_id", "String"),
    ("version_id", "String"),
    ("version", "String"),
    ("supersedes", "String"),
    ("status", "String"),
    ("tactic_type", "String"),
    ("model_role", "String"),
    ("label", "String"),
    ("notes", "String"),
    ("created_utc", "String"),
    ("effective_from_utc", "String"),
    ("effective_to_utc", "String"),
    ("org_id", "String"),
    ("author", "String"),
    ("visibility", "String"),
    ("simulate", "String"),
    ("source", "String"),
    ("source_file", "String"),
]

TACTIC_FIELD_NAMES = [name for name, _kind in TACTIC_FIELDS]

#: Fields that constitute a meaningful change (used for content hashing).
HASH_FIELDS = ("tactic_type", "notes", "effective_from_utc",
               "effective_to_utc", "org_id", "visibility", "simulate")

# --- sync defaults (overridable in the toolbox's Configure Settings tool) --- #
DEFAULT_S3_BUCKET = "paladin-user-disturbance"
DEFAULT_S3_REGION = "us-east-1"
DEFAULT_ACCESS_KEY = ""
DEFAULT_SECRET_KEY = ""
DEFAULT_SESSION_TOKEN = ""
DEFAULT_DISTURBANCE_PREFIX = "disturbances"
ORG_PREFIX_SEGMENT = "orgs"


def disturbance_prefix_for(org_id, fallback=None):
    """S3 key prefix for an organization's tactics.

    Must match the QGIS plugin's config.disturbance_prefix_for() exactly, or a
    Pro user and a QGIS user in the same org would write to different paths.
    """
    org = (org_id or "").strip().strip("/")
    if not org:
        return (fallback or DEFAULT_DISTURBANCE_PREFIX).strip("/")
    return "%s/%s/%s" % (DEFAULT_DISTURBANCE_PREFIX, ORG_PREFIX_SEGMENT, org)
DEFAULT_ORG_ID = ""
