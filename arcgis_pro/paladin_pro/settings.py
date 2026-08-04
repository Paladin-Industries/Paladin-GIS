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
"""Settings persistence.

QGIS uses QgsSettings; Pro geoprocessing tools have no session store, so
settings live in a JSON file under the user profile:
    %APPDATA%\\PaladinGIS\\settings.json
"""
import json
import os

from paladin_core import schema

_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                    "PaladinGIS")
_PATH = os.path.join(_DIR, "settings.json")

DEFAULTS = {
    "bucket": schema.DEFAULT_S3_BUCKET,
    "region": schema.DEFAULT_S3_REGION,
    "access_key": schema.DEFAULT_ACCESS_KEY,
    "secret_key": schema.DEFAULT_SECRET_KEY,
    "session_token": schema.DEFAULT_SESSION_TOKEN,
    "org_id": schema.DEFAULT_ORG_ID,
    "author": "",
    "visibility": schema.DEFAULT_VISIBILITY,
    "disturbance_prefix": schema.DEFAULT_DISTURBANCE_PREFIX,
}


def settings_path():
    return _PATH


def load_settings():
    values = dict(DEFAULTS)
    try:
        with open(_PATH, "r", encoding="utf-8") as fh:
            values.update(json.load(fh) or {})
    except (OSError, ValueError):
        pass
    return values


def save_settings(values):
    merged = load_settings()
    merged.update({k: v for k, v in values.items() if v is not None})
    os.makedirs(_DIR, exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2)
    return merged
