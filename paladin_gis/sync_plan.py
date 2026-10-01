# -*- coding: utf-8 -*-
# Paladin GIS - QGIS plugin for wildfire tactic authoring and sync
# Copyright (C) 2026 Paladin Industries, Inc.
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version.
"""Pure sync-planning helpers (no QGIS imports -> unit-testable).

Shared logic with the ArcGIS Pro toolbox (PaladinGIS.pyt). Keep in lockstep.
"""

import uuid


def parse_key(prefix, key):
    """-> (lineage, version:int, version_id).

    {prefix}/{lineage}/{version:06d}-{version_id}.geojson, or the legacy flat
    {prefix}/{lineage}.geojson (single version). 0.3.0 kept the ".geojson"
    suffix on legacy lineages, so they never matched and re-downloaded forever.
    """
    rel = key[len(prefix):] if prefix and key.startswith(prefix) else key
    rel = rel.lstrip("/")
    if "/" in rel:
        lineage, fname = rel.split("/", 1)
        stem = fname.rsplit("/", 1)[-1]
        if stem.endswith(".geojson"):
            stem = stem[: -len(".geojson")]
        head, _, vid = stem.partition("-")
        try:
            return lineage, int(head), vid
        except ValueError:
            return lineage, 1, vid
    lineage = rel[: -len(".geojson")] if rel.endswith(".geojson") else rel
    return lineage, 1, ""


def index_remote(prefix, keys):
    """{lineage: {"version", "version_id", "key"}} for each lineage's current version."""
    out = {}
    for k in keys:
        lineage, ver, vid = parse_key(prefix, k)
        cur = out.get(lineage)
        if cur is None or (ver, k) > (cur["version"], cur["key"]):
            out[lineage] = {"version": ver, "version_id": vid, "key": k}
    return out


def resolve_version(hint, state, remote_entry):
    """Pick (version, supersedes) for an upload.

    version    = max(local plan, remote current + 1): never reuse a version
                 number someone else already wrote (0.3.0 forked lineages).
    supersedes = the version actually current upstream if it's newer than
                 ours, else our last synced version.
    """
    version = hint
    if remote_entry is not None:
        version = max(version, remote_entry["version"] + 1)
    if remote_entry is not None and (state is None or remote_entry["version"] > state["version"]):
        supersedes = remote_entry["version_id"]
    else:
        supersedes = state["version_id"] if state else ""
    return version, supersedes


def stamp(obj, *, version, version_id, supersedes, created_utc, author, status):
    """Write version fields into a single-feature FeatureCollection (in place)."""
    feat = obj["features"][0]
    props = feat["properties"]
    props.update(version=str(version), version_id=version_id, supersedes=supersedes,
                 created_utc=created_utc, author=author or props.get("author", ""),
                 status=status)
    feat["id"] = props.get("tactic_id", feat.get("id"))
    return obj


def object_key(prefix, lineage, version, version_id):
    p = prefix.strip("/")
    return ("%s/%s/%06d-%s.geojson" % (p, lineage, version, version_id) if p
            else "%s/%06d-%s.geojson" % (lineage, version, version_id))


def new_version_id():
    return str(uuid.uuid4())
