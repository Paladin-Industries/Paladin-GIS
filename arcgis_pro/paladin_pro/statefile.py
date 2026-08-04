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
"""Per-tactic sync state: {tactic_id: {version, version_id, hash}}.

QGIS keeps this in memory for the session. GP tools do not persist between
runs, so Pro writes it to a sidecar JSON beside the tactics geodatabase —
which is strictly better: edits made yesterday still diff correctly today.
"""
import json
import os


def state_path(gdb_path):
    return os.path.join(os.path.dirname(gdb_path), "paladin_sync_state.json")


def load_state(gdb_path):
    try:
        with open(state_path(gdb_path), "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def save_state(gdb_path, state):
    with open(state_path(gdb_path), "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def get_sync_state(state, tactic_id):
    return state.get(tactic_id)


def set_sync_state(state, tactic_id, version, version_id, content_hash):
    state[tactic_id] = {"version": int(version), "version_id": version_id,
                        "hash": content_hash}


def current_versions(state):
    return {tid: st["version"] for tid, st in state.items()}
