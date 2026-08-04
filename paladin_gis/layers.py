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
"""Loading of external layers: LANDFIRE image services, imported vector files,
and read-down disturbance layers."""

import os

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsLayerTreeGroup,
    QgsMessageLog,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)

from . import config, tactics


def _log(msg, level=Qgis.MessageLevel.Info):
    QgsMessageLog.logMessage(str(msg), "Paladin", level)


def _project_crs_authid():
    crs = QgsProject.instance().crs()
    return crs.authid() if crs.isValid() else "EPSG:3857"


def _get_or_create_group(name):
    root = QgsProject.instance().layerTreeRoot()
    grp = root.findGroup(name)
    if grp is None:
        grp = root.insertGroup(0, name)
    return grp


# --------------------------------------------------------------------------- #
# LANDFIRE image services
# --------------------------------------------------------------------------- #
def load_landfire(entry):
    """Load a LANDFIRE ImageServer entry (from landfire_catalog) as a raster.

    Returns the QgsRasterLayer on success, else None.

    ArcGIS ImageServer is loaded through the `arcgismapserver` provider. Modern
    QGIS (>= ~3.26) detects ImageServer vs MapServer and calls the right export
    endpoint. If you're on an older build and this fails, the fallback is to
    enable WMS on the service or use the WCS mirror.
    """
    url = entry["url"]
    provider = entry.get("provider", "arcgismapserver")
    crs_authid = _project_crs_authid()

    uri = "crs='{crs}' format='PNG32' url='{url}'".format(crs=crs_authid, url=url)
    layer = QgsRasterLayer(uri, entry["label"], provider)

    if not layer.isValid():
        # Retry once forcing Web Mercator, the most broadly supported export SR.
        uri = "crs='EPSG:3857' format='PNG32' url='{url}'".format(url=url)
        layer = QgsRasterLayer(uri, entry["label"], provider)

    if not layer.isValid():
        _log("LANDFIRE layer failed to load: %s (%s)" % (entry["label"], url),
             Qgis.MessageLevel.Critical)
        return None

    QgsProject.instance().addMapLayer(layer, addToLegend=False)
    _get_or_create_group("LANDFIRE").addLayer(layer)
    _log("Loaded LANDFIRE layer: %s" % entry["label"])
    return layer


# --------------------------------------------------------------------------- #
# File import  (GeoJSON / KML / KMZ)
# --------------------------------------------------------------------------- #
_SUPPORTED_EXT = {".geojson", ".json", ".kml", ".kmz"}


def open_vector_file(path):
    """Open a GeoJSON/KML/KMZ file as an OGR vector layer for iteration.

    Returns (layer, src_crs) or (None, None). Caller iterates features and
    routes each into the tactic store with a chosen tactic type. KMZ is read
    directly by GDAL/OGR (it unzips internally).
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in _SUPPORTED_EXT:
        _log("Unsupported import extension: %s" % ext, Qgis.MessageLevel.Warning)
        return None, None

    layer = QgsVectorLayer(path, os.path.basename(path), "ogr")
    if not layer.isValid():
        _log("Import failed (invalid layer): %s" % path, Qgis.MessageLevel.Critical)
        return None, None

    src_crs = layer.crs()
    if not src_crs.isValid():
        # KML is always WGS84; assume it when the driver doesn't report a CRS.
        src_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    return layer, src_crs


def import_tactics_from_file(path, tactic_type, store, *, org_id="", author="",
                             visibility=None, simulate=True):
    """Import every feature in `path` as a tactic of `tactic_type`.

    Returns the number of tactics added.
    """
    layer, src_crs = open_vector_file(path)
    if layer is None:
        return 0

    added = 0
    fname = os.path.basename(path)
    for feat in layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        tid = store.add_tactic(
            tactic_type, geom, src_crs,
            org_id=org_id, author=author, visibility=visibility,
            simulate=simulate, source="imported", source_file=fname,
        )
        if tid:
            added += 1
    _log("Imported %d tactic(s) from %s as %s" % (added, fname, tactic_type))
    return added


# --------------------------------------------------------------------------- #
# Read-down disturbance layers
# --------------------------------------------------------------------------- #
def add_disturbance_layer(name, geojson_obj):
    """Create a read-down memory layer from a disturbance GeoJSON object.

    Disturbances pulled from S3 are other users' contributions; we surface them
    as separate, visually distinct layers grouped under "Paladin Disturbances"
    so they're never confused with the local (editable) tactic layers.
    """
    from qgis.core import QgsFeature  # local import keeps module import light

    features = tactics._iter_geojson_features(geojson_obj)
    if not features:
        return None

    # Infer geometry kind from the first feature.
    first_geom = (features[0].get("geometry") or {}).get("type", "")
    is_area = "Polygon" in first_geom
    wkb = "MultiPolygon" if is_area else "MultiLineString"

    layer = QgsVectorLayer("{wkb}?crs=EPSG:4326".format(wkb=wkb), name, "memory")
    from qgis.core import QgsField, QgsFields
    from qgis.PyQt.QtCore import QMetaType
    fields = QgsFields()
    for fname, _ in config.TACTIC_FIELDS:
        fields.append(QgsField(fname, QMetaType.Type.QString))
    layer.dataProvider().addAttributes(fields.toList())
    layer.updateFields()

    feats = tactics.geojson_to_features(geojson_obj, layer.fields())
    if feats:
        layer.dataProvider().addFeatures(feats)
    layer.updateExtents()

    QgsProject.instance().addMapLayer(layer, addToLegend=False)
    _get_or_create_group("Paladin Disturbances").addLayer(layer)
    return layer
