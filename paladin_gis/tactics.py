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
"""Tactic store: in-canvas memory layers + GeoJSON (de)serialization.

Two QGIS memory layers hold the user's tactics so they render, select, and edit
natively:
    * "Paladin Tactics (Areas)"  -> polygon tactics (fuel breaks, Rx burns)
    * "Paladin Tactics (Lines)"  -> line tactics    (scratch/dozer/handline)

Every feature carries the full metadata contract as attributes (see
config.TACTIC_FIELDS). Serialization transforms geometry to EPSG:4326 and emits
one GeoJSON Feature per tactic; import does the reverse.
"""

import json
import uuid
from datetime import datetime, timezone

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMessageLog,
    QgsProject,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QMetaType

from . import config

_AREA_LAYER_NAME = "Paladin Tactics (Areas)"
_LINE_LAYER_NAME = "Paladin Tactics (Lines)"

_FIELD_TYPE = {"String": QMetaType.Type.QString, "Int": QMetaType.Type.Int, "Double": QMetaType.Type.Double}
_FIELD_TYPE_BY_NAME = dict(config.TACTIC_FIELDS)


def _coerce_prop(name, raw):
    """Coerce a (stringified) GeoJSON property to its memory-layer field type.

    Payload properties are all strings on the wire; numeric fields such as
    `line_width_m` must come back as numbers so the memory layer, the content
    hash, and re-serialization stay consistent. Empty -> NULL for numerics,
    "" for strings (matching the prior stringify behavior).
    """
    ftype = _FIELD_TYPE_BY_NAME.get(name, "String")
    if ftype == "Double":
        s = "" if raw is None else str(raw).strip().replace(",", ".")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    if ftype == "Int":
        s = "" if raw is None else str(raw).strip()
        if not s:
            return None
        try:
            return int(float(s))
        except ValueError:
            return None
    return "" if raw is None else str(raw)


def _log(msg, level=Qgis.MessageLevel.Info):
    QgsMessageLog.logMessage(str(msg), "Paladin", level)


# Sentinel so update_attributes() can tell "leave unchanged" from "clear to None".
_UNSET = object()


def plan_version(state, status, content_hash):
    """Decide the next sync op for one tactic. Pure -> unit-testable.

    `state` is the last synced state ({"version", "version_id", "hash"}) or None
    if never synced. Returns (version:int, op:str) with op in
    {"create","edit","delete"}, or None to skip (unchanged, or a delete of
    something never synced).

    The delete branch is why `state` must survive reloads (it's persisted): if a
    reload dropped it to None, an inactive tactic would return None here and the
    tombstone would never be written — the #5 "delete does nothing" bug.
    """
    inactive = status == config.STATUS_INACTIVE
    if inactive:
        if state is None:
            return None                       # deleted before ever syncing
        return state["version"] + 1, "delete"
    if state is None:
        return 1, "create"
    if content_hash != state["hash"]:
        return state["version"] + 1, "edit"
    return None                               # unchanged since last sync


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def content_hash(feature):
    """Stable hash of the parts of a tactic that constitute a meaningful change.

    Geometry is compared at ~1cm precision so float noise doesn't spuriously
    bump versions. Version/identity/audit fields are intentionally excluded.
    """
    import hashlib
    geom = feature.geometry()
    parts = [geom.asWkt(7) if geom and not geom.isEmpty() else ""]
    for name in ("tactic_type", "notes", "effective_from_utc",
                 "effective_to_utc", "org_id", "visibility", "simulate",
                 "line_width_m"):
        val = feature[name]
        parts.append("" if val is None else str(val))
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def _build_fields():
    fields = QgsFields()
    for name, ftype in config.TACTIC_FIELDS:
        fields.append(QgsField(name, _FIELD_TYPE.get(ftype, QMetaType.Type.QString)))
    return fields


# --------------------------------------------------------------------------- #
# Memory layers
# --------------------------------------------------------------------------- #
class TacticStore:
    """Owns the two memory layers and mediates add/list/remove/serialize."""

    def __init__(self):
        self._area_layer = None
        self._line_layer = None
        # tactic_id -> {"version": int, "version_id": str, "hash": str}
        # Change-detection bookkeeping for sync. Persisted to QgsSettings so it
        # survives QGIS/plugin reloads — otherwise a reload loses every tactic's
        # synced version, which makes a post-reload delete get skipped (its state
        # reads as None before the read-down restores it) and causes duplicate
        # version writes to the append-only store.
        self._sync_state = self._load_sync_state()

    _SYNC_STATE_KEY = "sync_state_json"

    def _load_sync_state(self):
        from qgis.core import QgsSettings
        s = QgsSettings()
        raw = s.value("%s/%s" % (config.SETTINGS_GROUP, self._SYNC_STATE_KEY), "", str)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    def _save_sync_state(self):
        from qgis.core import QgsSettings
        s = QgsSettings()
        try:
            s.setValue("%s/%s" % (config.SETTINGS_GROUP, self._SYNC_STATE_KEY),
                       json.dumps(self._sync_state))
        except (TypeError, ValueError):
            pass

    # -- layer lifecycle ---------------------------------------------------- #
    def _ensure_layer(self, geom_kind):
        """geom_kind: 'area' | 'line'. Create the memory layer if needed."""
        is_area = geom_kind == "area"
        cached = self._area_layer if is_area else self._line_layer
        if cached is not None and self._layer_alive(cached):
            return cached

        wkb = "MultiPolygon" if is_area else "MultiLineString"
        name = _AREA_LAYER_NAME if is_area else _LINE_LAYER_NAME
        # Memory layers store in EPSG:4326 so what we render == what we ship.
        layer = QgsVectorLayer(
            "{wkb}?crs=EPSG:4326".format(wkb=wkb), name, "memory"
        )
        layer.dataProvider().addAttributes(_build_fields().toList())
        layer.updateFields()
        QgsProject.instance().addMapLayer(layer)

        if is_area:
            self._area_layer = layer
        else:
            self._line_layer = layer
        return layer

    @staticmethod
    def _layer_alive(layer):
        try:
            return QgsProject.instance().mapLayer(layer.id()) is not None
        except RuntimeError:
            return False

    def layers(self):
        out = []
        if self._area_layer is not None and self._layer_alive(self._area_layer):
            out.append(self._area_layer)
        if self._line_layer is not None and self._layer_alive(self._line_layer):
            out.append(self._line_layer)
        return out

    # -- adding tactics ----------------------------------------------------- #
    def add_tactic(self, tactic_type, geometry, source_crs, *,
                   notes="", effective_from=None, effective_to=None,
                   org_id="", author="", visibility=None, simulate=True,
                   source="drawn", source_file="", geometry_mode=None,
                   line_width_m=None):
        """Add one tactic. `geometry` is a QgsGeometry in `source_crs`.

        `geometry_mode` ("area" | "line") overrides the tactic type's default
        `category`; when None, the type's default is used (back-compat). For a
        line feature, `line_width_m` records the real-world footprint width in
        metres (recorded only, not applied to the geometry here).

        Returns the tactic_id, or None on failure.
        """
        spec = config.TACTIC_TYPES.get(tactic_type)
        if spec is None:
            _log("Unknown tactic type: %s" % tactic_type, Qgis.MessageLevel.Warning)
            return None

        geom_4326 = transform_geometry(geometry, source_crs, config.WGS84)
        if geom_4326 is None or geom_4326.isEmpty():
            _log("Tactic geometry empty after reprojection", Qgis.MessageLevel.Warning)
            return None

        geom_kind = geometry_mode if geometry_mode in ("area", "line") \
            else ("area" if spec["category"] == "area" else "line")
        # Width is meaningful only for line features. Ignore it for polygons so a
        # stray value can't ride along and confuse the consumer.
        width_val = None
        if geom_kind == "line" and line_width_m not in (None, ""):
            try:
                width_val = float(line_width_m)
            except (TypeError, ValueError):
                width_val = None
        # Normalize to Multi* so single- and multi-part inputs share a layer.
        geom_4326.convertToMultiType()

        layer = self._ensure_layer(geom_kind)
        feat = QgsFeature(layer.fields())
        feat.setGeometry(geom_4326)

        tactic_id = str(uuid.uuid4())
        values = {
            "tactic_id": tactic_id,
            "version_id": "",           # assigned at first sync
            "version": "",              # assigned at first sync
            "supersedes": "",
            "status": config.STATUS_ACTIVE,
            "tactic_type": tactic_type,
            "model_role": spec["model_role"],
            "label": spec["label"],
            "notes": notes or "",
            "created_utc": utc_now_iso(),
            "effective_from_utc": effective_from or "",
            "effective_to_utc": effective_to or "",
            "org_id": org_id or "",
            "author": author or "",
            "visibility": visibility or config.DEFAULT_VISIBILITY,
            "simulate": "true" if simulate else "false",
            "source": source,
            "source_file": source_file or "",
            "line_width_m": width_val,   # None (NULL) for polygons
        }
        for name, val in values.items():
            feat.setAttribute(name, val)

        ok, _ = layer.dataProvider().addFeatures([feat])
        if not ok:
            _log("addFeatures failed for tactic %s" % tactic_id, Qgis.MessageLevel.Critical)
            return None
        layer.updateExtents()
        layer.triggerRepaint()
        _apply_style(layer, tactic_type)
        return tactic_id

    # -- listing / removal -------------------------------------------------- #
    def all_tactics(self):
        """Yield (layer, feature) for every stored tactic."""
        for layer in self.layers():
            for feat in layer.getFeatures():
                yield layer, feat

    def remove_tactic(self, tactic_id):
        for layer in self.layers():
            ids = [f.id() for f in layer.getFeatures()
                   if f["tactic_id"] == tactic_id]
            if ids:
                layer.dataProvider().deleteFeatures(ids)
                layer.triggerRepaint()
                self._sync_state.pop(tactic_id, None)
                self._save_sync_state()
                return True
        return False

    def count(self):
        return sum(1 for _ in self.all_tactics())

    # -- versioning bookkeeping -------------------------------------------- #
    def get_sync_state(self, tactic_id):
        """Return {"version": int, "version_id": str, "hash": str} or None."""
        return self._sync_state.get(tactic_id)

    def set_sync_state(self, tactic_id, version, version_id, content_hash):
        self._sync_state[tactic_id] = {
            "version": int(version), "version_id": version_id,
            "hash": content_hash}
        self._save_sync_state()

    def _find(self, tactic_id):
        for layer in self.layers():
            for feat in layer.getFeatures():
                if feat["tactic_id"] == tactic_id:
                    return layer, feat
        return None, None

    def _set_attrs(self, tactic_id, attrs):
        """Write attribute values onto the stored feature by name."""
        layer, feat = self._find(tactic_id)
        if feat is None:
            return False
        idx = {name: layer.fields().indexFromName(name) for name in attrs}
        changes = {feat.id(): {idx[name]: val for name, val in attrs.items()}}
        ok = layer.dataProvider().changeAttributeValues(changes)
        layer.triggerRepaint()
        return ok

    def set_version_fields(self, tactic_id, *, version_id, version, supersedes,
                           status, created_utc, author):
        return self._set_attrs(tactic_id, {
            "version_id": version_id,
            "version": str(version),
            "supersedes": supersedes or "",
            "status": status,
            "created_utc": created_utc,
            "author": author or "",
        })

    def mark_inactive(self, tactic_id):
        """Soft-delete: flag for a tombstone version on next sync."""
        return self._set_attrs(tactic_id, {"status": config.STATUS_INACTIVE})

    def update_attributes(self, tactic_id, *, visibility=None, simulate=None,
                          notes=None, effective_from=None, effective_to=None,
                          line_width_m=_UNSET):
        """Edit metadata on an existing tactic (drawn OR imported).

        Only the arguments you pass are changed. These fields are part of the
        content hash, so an edit here makes the next Sync record a new version
        in the same lineage — no delete-and-recreate needed. Geometry is edited
        separately via the Vertex Tool.

        `line_width_m` only applies to line features; pass a float to set it,
        None to clear it, or leave it unset to keep the current value. Setting a
        width on a polygon feature is ignored.
        """
        layer, feat = self._find(tactic_id)
        if feat is None:
            return False
        attrs = {}
        if visibility is not None:
            if visibility not in config.VISIBILITY_LEVELS:
                _log("Refusing invalid visibility %r" % visibility,
                     Qgis.MessageLevel.Warning)
                return False
            attrs["visibility"] = visibility
        if simulate is not None:
            attrs["simulate"] = "true" if simulate else "false"
        if notes is not None:
            attrs["notes"] = notes
        if effective_from is not None:
            attrs["effective_from_utc"] = effective_from
        if effective_to is not None:
            attrs["effective_to_utc"] = effective_to
        if line_width_m is not _UNSET:
            is_line = layer.geometryType() == Qgis.GeometryType.Line
            if is_line:
                if line_width_m in (None, ""):
                    attrs["line_width_m"] = None
                else:
                    try:
                        attrs["line_width_m"] = float(line_width_m)
                    except (TypeError, ValueError):
                        _log("Ignoring non-numeric width %r" % line_width_m,
                             Qgis.MessageLevel.Warning)
        if not attrs:
            return False
        return self._set_attrs(tactic_id, attrs)

    def current_versions(self):
        """{tactic_id: version_int} for everything we currently hold synced."""
        return {tid: st["version"] for tid, st in self._sync_state.items()}

    # -- ingest read-down tactics into the editable store ------------------- #
    def reconcile_downloaded(self, obj):
        """Merge one downloaded current-version payload into the local store.

        Returns "added" | "updated" | "removed" | "conflict" | "skipped".
          added    : new lineage, now on canvas + editable.
          updated  : we had an older version and no local edits -> refreshed.
          removed  : upstream current version is a tombstone -> dropped locally.
          conflict : we have unsynced local edits -> kept local, upstream ignored.
        """
        feats = _iter_geojson_features(obj)
        if not feats:
            return "skipped"
        props = feats[0].get("properties") or {}
        geom_json = feats[0].get("geometry")
        lineage = props.get("tactic_id")
        if not lineage or geom_json is None:
            return "skipped"

        dl_version = int(props.get("version") or 1)
        dl_status = props.get("status") or config.STATUS_ACTIVE
        _layer, feat = self._find(lineage)

        if dl_status == config.STATUS_INACTIVE:
            if feat is not None:
                self.remove_tactic(lineage)
                return "removed"
            return "skipped"

        geom = _payload_geometry(geom_json)
        if geom is None:
            return "skipped"

        if feat is None:
            self._add_payload(props, geom)
            _l, f2 = self._find(lineage)
            self.set_sync_state(lineage, dl_version,
                                props.get("version_id", ""), content_hash(f2))
            return "added"

        state = self._sync_state.get(lineage)
        if state is None or content_hash(feat) != state["hash"]:
            return "conflict"   # protect local unsynced edits
        if dl_version > state["version"]:
            self._update_payload(lineage, props, geom)
            _l, f2 = self._find(lineage)
            self.set_sync_state(lineage, dl_version,
                                props.get("version_id", ""), content_hash(f2))
            return "updated"
        return "skipped"

    def _add_payload(self, props, geom):
        kind = "area" if geom.type() == Qgis.GeometryType.Polygon else "line"
        layer = self._ensure_layer(kind)
        feat = QgsFeature(layer.fields())
        feat.setGeometry(geom)
        for name, _t in config.TACTIC_FIELDS:
            feat.setAttribute(name, _coerce_prop(name, props.get(name)))
        layer.dataProvider().addFeatures([feat])
        layer.updateExtents()
        layer.triggerRepaint()
        _apply_style(layer, props.get("tactic_type", ""))

    def _update_payload(self, lineage, props, geom):
        layer, feat = self._find(lineage)
        if feat is None:
            return False
        layer.dataProvider().changeGeometryValues({feat.id(): geom})
        idx = {n: layer.fields().indexFromName(n) for n, _t in config.TACTIC_FIELDS}
        layer.dataProvider().changeAttributeValues(
            {feat.id(): {idx[n]: _coerce_prop(n, props.get(n))
                         for n, _t in config.TACTIC_FIELDS}})
        layer.triggerRepaint()
        return True


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #
def transform_geometry(geom, src_crs, dst_crs):
    """Return a copy of `geom` reprojected src->dst, or None on failure.

    `src_crs`/`dst_crs` may be QgsCoordinateReferenceSystem or an auth id str.
    """
    if geom is None or geom.isEmpty():
        return None
    src = _as_crs(src_crs)
    dst = _as_crs(dst_crs)
    if not src.isValid() or not dst.isValid():
        return None
    if src == dst:
        return QgsGeometry(geom)
    xform = QgsCoordinateTransform(src, dst, QgsProject.instance())
    out = QgsGeometry(geom)
    try:
        out.transform(xform)
    except Exception as exc:  # noqa: BLE001 - transform raises QgsCsException
        _log("CRS transform failed: %s" % exc, Qgis.MessageLevel.Warning)
        return None
    return out


def _as_crs(crs):
    if isinstance(crs, QgsCoordinateReferenceSystem):
        return crs
    return QgsCoordinateReferenceSystem(crs)


# --------------------------------------------------------------------------- #
# Serialization  (memory feature -> GeoJSON dict, one Feature per tactic)
# --------------------------------------------------------------------------- #
def feature_to_geojson(feature):
    """Serialize one memory-layer feature (already EPSG:4326) to a GeoJSON
    Feature dict with the Paladin property contract."""
    geom = feature.geometry()
    geometry = json.loads(geom.asJson()) if geom and not geom.isEmpty() else None

    props = {"paladin_schema": config.TACTIC_SCHEMA, "crs": config.WGS84}
    for name, _ in config.TACTIC_FIELDS:
        val = feature[name]
        props[name] = "" if val is None else str(val)

    return {
        "type": "Feature",
        "id": props.get("tactic_id"),
        "geometry": geometry,
        "properties": props,
    }


def geojson_to_features(geojson_obj, layer_fields):
    """Parse a GeoJSON FeatureCollection/Feature into QgsFeatures for a
    read-down disturbance layer. Geometry assumed EPSG:4326."""
    feats = []
    features = _iter_geojson_features(geojson_obj)
    for gj in features:
        geom = QgsGeometry.fromWkt(_geojson_geometry_to_wkt(gj.get("geometry")))
        if geom is None or geom.isEmpty():
            continue
        feat = QgsFeature(layer_fields)
        feat.setGeometry(geom)
        props = gj.get("properties", {}) or {}
        for name, _ in config.TACTIC_FIELDS:
            if name in props:
                feat.setAttribute(name, _coerce_prop(name, props[name]))
        feats.append(feat)
    return feats


def _iter_geojson_features(obj):
    if not isinstance(obj, dict):
        return []
    if obj.get("type") == "FeatureCollection":
        return obj.get("features", []) or []
    if obj.get("type") == "Feature":
        return [obj]
    return []


def _geojson_geometry_to_wkt(geometry):
    """Robust GeoJSON-geometry -> WKT via QgsGeometry.fromJson when available,
    falling back to a manual pass."""
    if geometry is None:
        return ""
    try:
        g = QgsGeometry.fromJson(json.dumps(geometry))  # QGIS >= 3.28
        if g is not None and not g.isEmpty():
            return g.asWkt()
    except Exception:  # noqa: BLE001
        pass
    # Minimal manual fallback for common types.
    try:
        gj = geometry
        t = gj["type"]
        c = gj["coordinates"]
        if t == "Point":
            return "POINT (%s %s)" % (c[0], c[1])
        if t == "LineString":
            return "LINESTRING (%s)" % _coords(c)
        if t == "MultiLineString":
            return "MULTILINESTRING (%s)" % ", ".join("(%s)" % _coords(p) for p in c)
        if t == "Polygon":
            return "POLYGON (%s)" % _rings(c)
        if t == "MultiPolygon":
            return "MULTIPOLYGON (%s)" % ", ".join("(%s)" % _rings(p) for p in c)
    except (KeyError, TypeError, IndexError):
        return ""
    return ""


def _coords(pts):
    return ", ".join("%s %s" % (p[0], p[1]) for p in pts)


def _rings(rings):
    return ", ".join("(%s)" % _coords(r) for r in rings)


def _payload_geometry(geom_json):
    """Build a normalized (Multi*) QgsGeometry from a GeoJSON geometry dict."""
    wkt = _geojson_geometry_to_wkt(geom_json)
    if not wkt:
        return None
    geom = QgsGeometry.fromWkt(wkt)
    if geom is None or geom.isEmpty():
        return None
    geom.convertToMultiType()
    return geom


# --------------------------------------------------------------------------- #
# Styling
# --------------------------------------------------------------------------- #
def _apply_style(layer, tactic_type):
    """Symbolize the memory layer using the tactic's configured color.

    We drive symbology by the `tactic_type` attribute via a categorized
    renderer so one layer can hold several tactic types cleanly.
    """
    from qgis.core import (
        QgsCategorizedSymbolRenderer,
        QgsRendererCategory,
        QgsSymbol,
    )
    from qgis.PyQt.QtGui import QColor

    is_area = layer.geometryType() == Qgis.GeometryType.Polygon
    categories = []
    # A tactic type may now appear in EITHER layer (a fuel break drawn as a
    # line, a dozer line drawn as a polygon), so we style every type in both
    # layers rather than filtering by the type's default `category`. The symbol
    # geometry follows the layer, so the styling stays correct either way.
    for key, spec in config.TACTIC_TYPES.items():
        sym = QgsSymbol.defaultSymbol(layer.geometryType())
        r, g, b, a = spec["color"]
        if is_area:
            sym.setColor(QColor(r, g, b, a))
            try:
                sym.symbolLayer(0).setStrokeColor(QColor(r, g, b, 255))
                sym.symbolLayer(0).setStrokeWidth(0.6)
            except Exception:  # noqa: BLE001
                pass
        else:
            sym.setColor(QColor(r, g, b, a))
            try:
                sym.setWidth(0.8)
            except Exception:  # noqa: BLE001
                pass
        categories.append(QgsRendererCategory(key, sym, spec["label"]))

    if categories:
        renderer = QgsCategorizedSymbolRenderer("tactic_type", categories)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
