# -*- coding: utf-8 -*-
# Paladin GIS - QGIS plugin for wildfire tactic authoring and sync
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
"""Static configuration for the Paladin GIS plugin.

Everything here is either a compile-time constant or a default that the user
can override in the plugin's Settings section (persisted via QgsSettings).
"""

from qgis.core import Qgis

# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
# Bump this string whenever the GeoJSON property contract changes. The fire
# model reads `paladin_schema` first and refuses payloads it doesn't understand,
# so this is the single source of truth for the wire format version.
# DELIBERATELY NOT bumped for `line_width_m`. That field is an ADDITIVE,
# optional property: existing consumers that key on "paladin.tactic.v3" keep
# accepting payloads and simply ignore the new property, and a width-aware
# consumer also accepts v3. Bumping to v4 would force a lockstep consumer deploy
# (the model "refuses payloads it doesn't understand") for zero benefit here.
# Bump only when you make a BREAKING change (rename/remove a field, or change a
# field's meaning). If/when the consumer starts buffering lines by width, that's
# still additive on the wire — no bump needed.
TACTIC_SCHEMA = "paladin.tactic.v3"

# CRS every payload is written in. Non-negotiable: the model expects lon/lat.
WGS84 = "EPSG:4326"

# --------------------------------------------------------------------------- #
# Versioning / audit
# --------------------------------------------------------------------------- #
# Append-only lineage model for full observability of decision-making:
#   tactic_id   -> stable lineage id, constant across every edit.
#   version_id  -> unique id of ONE immutable version (the S3 object identity).
#   version     -> monotonic integer, 1-based.
#   supersedes  -> the version_id this version replaces ("" for version 1).
#   status      -> "active" | "inactive" (inactive = soft delete / tombstone).
# Nothing is ever overwritten or hard-deleted; every version stays in the bucket.
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"

# --------------------------------------------------------------------------- #
# Access / simulation classification (two orthogonal axes)
# --------------------------------------------------------------------------- #
# visibility -> WHO may see the tactic. Enforced by the read path, not by the
#   file. On a shared folder with direct S3 keys this is honor-system; a backend
#   broker (or ABAC object tags) is what makes it real.
#     public  : any user of the platform
#     org     : only users in the same org_id
#     private : only the creator (author)
VISIBILITY_LEVELS = ["public", "org", "private"]
DEFAULT_VISIBILITY = "org"

# simulate -> WHETHER the sim engine should include this tactic. Independent of
#   visibility: a proposed tactic can be shared (public/org) but not simulated,
#   or carried out and simulated. False = proposed / do-not-simulate.
DEFAULT_SIMULATE = True

# --------------------------------------------------------------------------- #
# Tactic registry
# --------------------------------------------------------------------------- #
# `geometry`      -> Qgis.GeometryType used for capture + memory layer.
# `category`      -> "area" | "line". The DEFAULT geometry mode for this type.
#                    As of schema v3 the drawer can override this per feature
#                    (draw a fuel break as a centerline+width, a burn as a strip,
#                    etc.); `category` just pre-selects the Geometry toggle.
# `model_role`    -> how the fire model should treat this feature:
#                      "fuel_modification" : area edits composed into the fuelbed
#                      "control_line"      : candidate barrier / PCL line
#                    NOTE: model_role stays tied to the TYPE, not to the drawn
#                    geometry. A fuel break drawn as a line is still a
#                    fuel_modification whose footprint width is `line_width_m`;
#                    a dozer line is still a control_line whose barrier width is
#                    `line_width_m`. Either way, a line feature's real-world
#                    footprint is the centerline buffered by line_width_m. The
#                    SIM CONSUMER must perform that buffering — the plugin only
#                    records the width.
# `color`         -> RGBA render + rubber-band color.
#
# This dict is the ONLY place to add a tactic type. The panel builds its
# buttons from it, so a new entry gets a button, a capture tool, list support,
# and serialization for free.
TACTIC_TYPES = {
    "fuel_break": {
        "label": "Fuel Break",
        "geometry": Qgis.GeometryType.Polygon,
        "category": "area",
        "model_role": "fuel_modification",
        "color": (255, 176, 0, 90),      # amber
    },
    "prescribed_burn": {
        "label": "Prescribed Burn",
        "geometry": Qgis.GeometryType.Polygon,
        "category": "area",
        "model_role": "fuel_modification",
        "color": (214, 69, 65, 90),      # red
    },
    "scratch_line": {
        "label": "Scratch Line",
        "geometry": Qgis.GeometryType.Line,
        "category": "line",
        "model_role": "control_line",
        "color": (255, 214, 0, 220),     # yellow
    },
    "handline": {
        "label": "Handline",
        "geometry": Qgis.GeometryType.Line,
        "category": "line",
        "model_role": "control_line",
        "color": (0, 200, 120, 220),     # green
    },
    "dozer_line": {
        "label": "Dozer Line",
        "geometry": Qgis.GeometryType.Line,
        "category": "line",
        "model_role": "control_line",
        "color": (120, 90, 60, 230),     # brown
    },
    "hose_lay": {
        "label": "Hose Lay",
        "geometry": Qgis.GeometryType.Line,
        "category": "line",
        "model_role": "control_line",
        "color": (0, 150, 220, 220),     # water blue
    },
    "retardant": {
        "label": "Retardant",
        "geometry": Qgis.GeometryType.Polygon,
        "category": "area",              # drop footprint; can be drawn as a line
        "model_role": "fuel_modification",
        "color": (233, 30, 99, 120),     # retardant pink/magenta
    },
}

# Attribute schema for the in-canvas memory layers. Order matters: QgsFeature
# attributes are set positionally in tactics.py.
TACTIC_FIELDS = [
    ("tactic_id", "String"),         # stable lineage id
    ("version_id", "String"),        # this version's unique id (S3 object id)
    ("version", "String"),           # monotonic version number, 1-based
    ("supersedes", "String"),        # previous version_id ("" for v1)
    ("status", "String"),            # active | inactive
    ("tactic_type", "String"),
    ("model_role", "String"),
    ("label", "String"),
    ("notes", "String"),
    ("created_utc", "String"),       # when THIS version was created
    ("effective_from_utc", "String"),
    ("effective_to_utc", "String"),
    ("org_id", "String"),            # organization that owns the tactic
    ("author", "String"),            # who created this version
    ("visibility", "String"),        # public | org | private
    ("simulate", "String"),          # "true" | "false"
    ("source", "String"),            # "drawn" | "imported"
    ("source_file", "String"),
    ("line_width_m", "Double"),      # footprint width (m) for LINE features;
                                     # empty/NULL for polygon features
]

# --------------------------------------------------------------------------- #
# Geometry mode (per-feature override of a tactic type's default `category`)
# --------------------------------------------------------------------------- #
# The drawer picks one of these before capturing. When a tactic-type button is
# pressed the toggle snaps to that type's `category`, but the user may override.
#   "area" -> polygon capture, no width.
#   "line" -> polyline capture, `line_width_m` recorded and shipped.
GEOMETRY_MODES = [("Polygon", "area"), ("Line", "line")]

# Default width (metres) pre-filled when the user switches a draw to Line mode.
# Metres to match the physics model / SI convention. NOTE: fireline crews think
# in feet (a D6 dozer line ~3.5-4 m, handline ~0.5-1 m); if you want the UI in
# feet with an m conversion on write, that's a Settings toggle, not a schema
# change. Left in metres for now.
DEFAULT_LINE_WIDTH_M = 3.0

# --------------------------------------------------------------------------- #
# S3 / sync defaults (overridable in Settings)
# --------------------------------------------------------------------------- #
# Backend selection:
#   "s3native"  -> talk to S3 directly with AWS SigV4 signing implemented in
#                  pure standard library (sigv4.py). No third-party dependency,
#                  so customers install nothing. This is the shipping default.
#   "boto3"     -> same, but via boto3 if it's installed. Optional.
#   "presigned" -> broker through a Paladin API (token -> API -> MongoDB). Future.
DEFAULT_S3_BACKEND = "s3native"

# boto3 / native backend defaults.
DEFAULT_S3_BUCKET = "paladin-user-disturbance"
DEFAULT_S3_REGION = "us-east-1"
# Credentials default empty; each customer pastes the keys you emailed them.
DEFAULT_ACCESS_KEY = ""
DEFAULT_SECRET_KEY = ""
DEFAULT_SESSION_TOKEN = ""

# Presigned backend (future): Paladin endpoint that brokers S3 access.
DEFAULT_PALADIN_API_BASE = "https://api.paladin.example"

# One shared disturbance prefix for ALL orgs (no per-team folders). Access is
# governed by the per-file `visibility` + `org_id` metadata, honored by the read
# path — not by the key layout.
#   key: {DISTURBANCE_PREFIX}/{tactic_id}.geojson
DEFAULT_DISTURBANCE_PREFIX = "disturbances"

# Identity defaults (set per user in Settings).
DEFAULT_ORG_ID = ""

# QgsSettings group under which all overridable values are stored.
SETTINGS_GROUP = "paladin_gis"
