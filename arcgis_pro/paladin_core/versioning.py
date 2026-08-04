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
"""Append-only versioning — mirrors the QGIS plugin's sync logic exactly.

Layout:  {prefix}/{tactic_id}/{version:06d}-{version_id}.geojson
so the lexically greatest key inside a lineage folder is the current version.
Flat legacy keys ({prefix}/{id}.geojson) count as single-version lineages.

create -> v1 | edit -> v+1 supersedes prev | delete -> v+1 status=inactive.
Nothing is overwritten or hard-deleted.
"""
import hashlib

from . import schema


def content_hash(geometry_wkt, values):
    """Stable hash of the parts of a tactic that constitute a real change.

    Identity/audit fields are deliberately excluded so re-stamping a version
    does not itself look like an edit.
    """
    parts = [geometry_wkt or ""]
    for name in schema.HASH_FIELDS:
        val = values.get(name)
        parts.append("" if val is None else str(val))
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def decide_operation(state, current_hash, status):
    """What to upload for one tactic, or None if nothing changed.

    Returns (operation, version, supersedes).
    """
    if status == schema.STATUS_INACTIVE:
        if state is None:
            return None            # deleted before it ever synced
        return "delete", state["version"] + 1, state["version_id"]
    if state is None:
        return "create", 1, ""
    if current_hash != state["hash"]:
        return "edit", state["version"] + 1, state["version_id"]
    return None                    # unchanged since last sync


def object_key(prefix, tactic_id, version, version_id):
    name = "%06d-%s.geojson" % (int(version), version_id)
    prefix = (prefix or "").strip("/")
    return ("%s/%s/%s" % (prefix, tactic_id, name) if prefix
            else "%s/%s" % (tactic_id, name))


def version_from_key(key):
    head = key.rsplit("/", 1)[-1].split("-", 1)[0]
    try:
        return int(head)
    except ValueError:
        return 1                   # legacy flat file = single version


def current_version_keys(keys, prefix):
    """{lineage: key of its current version} from a flat key listing."""
    prefix = prefix or ""
    groups = {}
    for key in keys:
        rel = key[len(prefix):] if prefix and key.startswith(prefix) else key
        rel = rel.lstrip("/")
        lineage = rel.split("/", 1)[0] if "/" in rel else rel
        if lineage.endswith(".geojson"):
            lineage = lineage[:-len(".geojson")]
        cur = groups.get(lineage)
        if cur is None or key > cur:
            groups[lineage] = key
    return groups


def visible_to(props, my_org, my_author):
    """Client-side visibility filter — same rule as the QGIS read path."""
    vis = (props.get("visibility") or schema.DEFAULT_VISIBILITY).lower()
    if vis == "public":
        return True
    if vis == "org":
        return bool(my_org) and props.get("org_id") == my_org
    if vis == "private":
        return bool(my_author) and props.get("author") == my_author
    return False
