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
"""Push/pull orchestration — mirrors the QGIS plugin's SyncWorker.

GP tools already run off the UI thread, so this is plain synchronous code that
reports through a `log(msg)` callable wired to arcpy.AddMessage.

Semantics are shared with QGIS via paladin_core.versioning:
  create -> v1 | edit -> v+1 supersedes prev | delete -> v+1 tombstone
  pull   -> lexically greatest key per lineage, visibility-filtered, then
            reconciled as added / updated / removed / conflict / skipped,
            where local unsynced edits always win.
"""
import uuid

from paladin_core import geojson as core_geojson
from paladin_core import s3 as core_s3
from paladin_core import schema, versioning

from . import convert, statefile, store


def make_backend(settings):
    return core_s3.make_backend(settings)


def push(gdb, settings, state, log):
    """Upload every created / edited / deleted tactic. Returns (count, removed)."""
    author = (settings.get("author") or "").strip()
    if not author:
        raise core_s3.SyncError(
            "No author set. Run 'Configure Paladin Settings' first and use "
            "your Paladin account email.")

    backend = make_backend(settings)
    prefix = schema.disturbance_prefix_for(
        settings.get("org_id"), settings.get("disturbance_prefix"))
    now = store.utc_now_iso()

    uploads = 0
    removals = []
    for _fc, attrs, geom in list(store.iter_tactics(gdb)):
        lineage = attrs.get("tactic_id")
        if not lineage:
            continue
        current_hash = store.content_hash_row(geom, attrs)
        status = attrs.get("status") or schema.STATUS_ACTIVE

        decision = versioning.decide_operation(
            statefile.get_sync_state(state, lineage), current_hash, status)
        if decision is None:
            continue
        operation, version, supersedes = decision
        version_id = str(uuid.uuid4())

        stamped = dict(attrs)
        stamped.update({"version_id": version_id, "version": str(version),
                        "supersedes": supersedes, "status": status,
                        "created_utc": now, "author": author})
        store.update_fields(gdb, lineage, stamped)

        payload = core_geojson.collection(core_geojson.build_feature(
            lineage, convert.geometry_to_geojson(geom), stamped))
        key = versioning.object_key(prefix, lineage, version, version_id)
        log("Uploading %s v%d (%s)" % (
            stamped.get("label") or lineage[:8], version, operation))
        backend.put_geojson(key, payload)
        uploads += 1

        statefile.set_sync_state(state, lineage, version, version_id,
                                 current_hash)
        if operation == "delete":
            removals.append(lineage)

    for lineage in removals:
        store.remove_tactic(gdb, lineage)
        state.pop(lineage, None)
    return uploads, removals


def pull(gdb, settings, state, log):
    """Read down current versions, visibility-filter, reconcile. Returns counts."""
    backend = make_backend(settings)
    prefix = schema.disturbance_prefix_for(
        settings.get("org_id"), settings.get("disturbance_prefix"))
    list_prefix = (prefix + "/") if prefix else ""
    my_org = settings.get("org_id", "")
    my_author = settings.get("author", "")

    keys = backend.list_keys(list_prefix)
    current = versioning.current_version_keys(keys, list_prefix)
    log("Found %d lineage(s) upstream." % len(current))

    counts = {"added": 0, "updated": 0, "removed": 0, "conflict": 0,
              "skipped": 0}
    have = statefile.current_versions(state)

    for lineage, key in sorted(current.items()):
        version = versioning.version_from_key(key)
        if lineage in have and version <= have[lineage]:
            counts["skipped"] += 1
            continue
        payload = backend.get_geojson(key)
        props = core_geojson.first_props(payload)
        if not versioning.visible_to(props, my_org, my_author):
            counts["skipped"] += 1
            continue
        result = _reconcile(gdb, state, payload)
        counts[result] = counts.get(result, 0) + 1
    return counts


def _reconcile(gdb, state, payload):
    feats = core_geojson.iter_features(payload)
    if not feats:
        return "skipped"
    props = feats[0].get("properties") or {}
    geom_json = feats[0].get("geometry")
    lineage = props.get("tactic_id")
    if not lineage:
        return "skipped"

    downloaded_version = int(props.get("version") or 1)
    status = props.get("status") or schema.STATUS_ACTIVE
    existing = store.find_tactic(gdb, lineage)

    if status == schema.STATUS_INACTIVE:
        if existing is not None:
            store.remove_tactic(gdb, lineage)
            state.pop(lineage, None)
            return "removed"
        return "skipped"

    geom = convert.geojson_to_geometry(geom_json)
    if geom is None:
        return "skipped"

    if existing is None:
        if not store.add_from_payload(gdb, props, geom):
            return "skipped"
        _remember(state, lineage, downloaded_version, props, geom)
        return "added"

    attrs, current_geom = existing
    known = statefile.get_sync_state(state, lineage)
    if known is None or store.content_hash_row(current_geom,
                                               attrs) != known["hash"]:
        return "conflict"            # protect local unsynced edits
    if downloaded_version > known["version"]:
        store.update_payload(gdb, lineage, props, geom)
        _remember(state, lineage, downloaded_version, props, geom)
        return "updated"
    return "skipped"


def _remember(state, lineage, version, props, geom):
    attrs = {n: props.get(n, "") for n in store.field_names()}
    statefile.set_sync_state(state, lineage, version,
                             props.get("version_id", ""),
                             store.content_hash_row(geom, attrs))


def sync(settings, log):
    """Full round-trip. Returns (uploaded, download counts)."""
    gdb = store.project_gdb()
    state = statefile.load_state(gdb)
    try:
        uploaded, _removed = push(gdb, settings, state, log)
        counts = pull(gdb, settings, state, log)
    finally:
        statefile.save_state(gdb, state)
    return uploaded, counts
