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
"""The Paladin dock panel and its sync worker."""

import uuid

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsMessageLog,
    QgsProject,
    QgsRectangle,
    QgsSettings,
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QCheckBox,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import config, landfire_catalog, layers, s3_client, tactics


def _log(msg, level=Qgis.MessageLevel.Info):
    QgsMessageLog.logMessage(str(msg), "Paladin", level)


def _first_props(obj):
    """Properties of the first feature in a FeatureCollection/Feature dict."""
    try:
        if obj.get("type") == "FeatureCollection":
            feats = obj.get("features") or []
            return (feats[0].get("properties") or {}) if feats else {}
        if obj.get("type") == "Feature":
            return obj.get("properties") or {}
    except AttributeError:
        pass
    return {}


# --------------------------------------------------------------------------- #
# Settings helpers
# --------------------------------------------------------------------------- #
def load_settings():
    s = QgsSettings()
    s.beginGroup(config.SETTINGS_GROUP)
    out = {
        "author": s.value("author", "", str),
        "org_id": s.value("org_id", config.DEFAULT_ORG_ID, str),
        "backend": s.value("backend", config.DEFAULT_S3_BACKEND, str),
        "api_base": s.value("api_base", config.DEFAULT_PALADIN_API_BASE, str),
        "token": s.value("token", "", str),
        "bucket": s.value("bucket", config.DEFAULT_S3_BUCKET, str),
        "region": s.value("region", config.DEFAULT_S3_REGION, str),
        "access_key": s.value("access_key", config.DEFAULT_ACCESS_KEY, str),
        "secret_key": s.value("secret_key", config.DEFAULT_SECRET_KEY, str),
        "session_token": s.value("session_token", config.DEFAULT_SESSION_TOKEN, str),
        "disturbance_prefix": s.value("disturbance_prefix",
                                      config.DEFAULT_DISTURBANCE_PREFIX, str),
    }
    s.endGroup()
    return out


def save_settings(values):
    s = QgsSettings()
    s.beginGroup(config.SETTINGS_GROUP)
    for k, v in values.items():
        s.setValue(k, v)
    s.endGroup()


# --------------------------------------------------------------------------- #
# Sync worker  (network I/O only; no QGIS layer creation off the main thread)
# --------------------------------------------------------------------------- #
class SyncWorker(QThread):
    progress = pyqtSignal(str)
    downloaded = pyqtSignal(object)        # current-version geojson payload
    finished_ok = pyqtSignal(int, int)     # (uploaded, downloaded)
    failed = pyqtSignal(str)

    def __init__(self, settings, uploads, list_prefix, have=None,
                 my_org="", my_author=""):
        super().__init__()
        self._settings = settings
        self._uploads = uploads            # list of (key, geojson_dict)
        self._prefix = list_prefix         # shared folder: disturbances/
        self._have = dict(have or {})      # lineage -> version we already hold
        self._my_org = my_org
        self._my_author = my_author

    def _visible_to_me(self, props):
        """Honor per-file visibility (client-side; the broker enforces for real)."""
        vis = (props.get("visibility") or "org").lower()
        if vis == "public":
            return True
        if vis == "org":
            return bool(self._my_org) and props.get("org_id") == self._my_org
        if vis == "private":
            return bool(self._my_author) and props.get("author") == self._my_author
        return False

    def run(self):
        try:
            backend = s3_client.make_backend(self._settings)
        except s3_client.SyncError as exc:
            self.failed.emit(str(exc))
            return

        up = 0
        try:
            for key, obj in self._uploads:
                self.progress.emit("Uploading %s" % key)
                backend.put_geojson(key, obj)
                up += 1

            self.progress.emit("Reading down disturbances...")
            keys = backend.list_keys(self._prefix)
            current = self._current_version_keys(keys)
            down = 0
            for lineage_key, key in current.items():
                ver = self._version_from_key(key)
                have_ver = self._have.get(lineage_key)
                if have_ver is not None and ver <= have_ver:
                    continue  # we already hold this version (or newer)
                obj = backend.get_geojson(key)
                props = _first_props(obj)
                if not self._visible_to_me(props):
                    continue
                # Emit active AND inactive (main thread reconciles: add / update
                # / remove). Reconciliation and QGIS layer edits run there.
                self.downloaded.emit(obj)
                down += 1
                self.progress.emit("Pulled %s v%s" % (lineage_key[:8], ver))
        except s3_client.SyncError as exc:
            self.failed.emit(str(exc))
            return

        self.finished_ok.emit(up, down)

    @staticmethod
    def _version_from_key(key):
        head = key.rsplit("/", 1)[-1].split("-", 1)[0]
        try:
            return int(head)
        except ValueError:
            return 1  # legacy flat file = single version

    def _current_version_keys(self, keys):
        """Group object keys by lineage and return {lineage: current_version_key}.

        Layout: {prefix}/{lineage}/{version:06d}-{version_id}.geojson, so the
        lexically greatest key within a lineage folder is the current version.
        Flat legacy keys ({prefix}/{id}.geojson) are treated as single-version
        lineages.
        """
        prefix = self._prefix
        groups = {}
        for k in keys:
            rel = k[len(prefix):] if prefix and k.startswith(prefix) else k
            rel = rel.lstrip("/")
            lineage_key = rel.split("/", 1)[0] if "/" in rel else rel
            cur = groups.get(lineage_key)
            if cur is None or k > cur:
                groups[lineage_key] = k
        return groups


# --------------------------------------------------------------------------- #
# Dock panel
# --------------------------------------------------------------------------- #
class PaladinDock(QDockWidget):
    def __init__(self, iface, store):
        super().__init__("Paladin", iface.mainWindow())
        self._iface = iface
        self._canvas = iface.mapCanvas()
        self._store = store
        self._capture_tool = None
        self._current_type = None
        self._worker = None
        self._editing_lineage = None
        self._editing_layer = None
        self._pending_removals = []
        self.setObjectName("PaladinDock")

        container = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        self.setWidget(scroll)

        root = QVBoxLayout(container)
        root.addWidget(self._build_landfire_group())
        root.addWidget(self._build_draw_group())
        root.addWidget(self._build_import_group())
        root.addWidget(self._build_list_group())
        root.addWidget(self._build_sync_group())
        root.addWidget(self._build_settings_group())
        root.addStretch(1)

        self.refresh_list()

    def set_capture_tool(self, tool):
        self._capture_tool = tool
        tool.captured.connect(self._on_captured)

    # ---- LANDFIRE ---------------------------------------------------------- #
    def _build_landfire_group(self):
        box = QGroupBox("LANDFIRE image services")
        lay = QVBoxLayout(box)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        for theme in landfire_catalog.themes():
            parent = QTreeWidgetItem([theme])
            parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._tree.addTopLevelItem(parent)
            for entry in landfire_catalog.entries_for_theme(theme):
                label = entry["label"]
                if not entry.get("verified", False):
                    label += "  (verify)"
                child = QTreeWidgetItem([label])
                child.setData(0, Qt.ItemDataRole.UserRole, entry["id"])
                parent.addChild(child)
            parent.setExpanded(True)
        self._tree.itemDoubleClicked.connect(self._on_landfire_double_click)
        lay.addWidget(self._tree)

        btn = QPushButton("Add selected layer")
        btn.clicked.connect(self._add_landfire_selected)
        lay.addWidget(btn)
        return box

    def _on_landfire_double_click(self, item, _col):
        self._load_landfire_item(item)

    def _add_landfire_selected(self):
        item = self._tree.currentItem()
        if item is not None:
            self._load_landfire_item(item)

    def _load_landfire_item(self, item):
        entry_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not entry_id:
            return
        entry = landfire_catalog.entry_by_id(entry_id)
        if entry is None:
            return
        layer = layers.load_landfire(entry)
        if layer is None:
            self._iface.messageBar().pushWarning(
                "Paladin", "Could not load %s. See the Log Messages (Paladin) "
                "panel; the service may be renamed or ImageServer support may "
                "need a newer QGIS." % entry["label"])

    # ---- Draw -------------------------------------------------------------- #
    def _build_draw_group(self):
        box = QGroupBox("Draw tactic")
        lay = QVBoxLayout(box)

        self._freehand = QCheckBox("Freehand (press-drag-release)")
        self._freehand.stateChanged.connect(self._on_freehand_toggle)
        lay.addWidget(self._freehand)

        grid = QGridLayout()
        for i, (key, spec) in enumerate(config.TACTIC_TYPES.items()):
            btn = QPushButton(spec["label"])
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c, k=key: self._start_draw(k))
            grid.addWidget(btn, i // 2, i % 2)
            spec["_button"] = btn  # keep a handle to un-check siblings
        lay.addLayout(grid)

        # Geometry mode + width. The toggle snaps to the picked type's default
        # category but can be overridden per draw; width applies to Line only.
        geom_row = QHBoxLayout()
        geom_row.addWidget(QLabel("Geometry:"))
        self._geom_mode = QComboBox()
        for label, mode in config.GEOMETRY_MODES:
            self._geom_mode.addItem(label, mode)
        self._geom_mode.currentIndexChanged.connect(self._on_geom_mode_changed)
        geom_row.addWidget(self._geom_mode)
        geom_row.addWidget(QLabel("Width (m):"))
        self._line_width = QLineEdit()
        self._line_width.setPlaceholderText("e.g. 3.0")
        self._line_width.setMaximumWidth(80)
        geom_row.addWidget(self._line_width)
        geom_row.addStretch(1)
        lay.addLayout(geom_row)
        self._sync_width_enabled()

        lay.addWidget(QLabel("Notes"))
        self._notes = QLineEdit()
        lay.addWidget(self._notes)

        time_row = QHBoxLayout()
        self._eff_from = QLineEdit()
        self._eff_from.setPlaceholderText("Effective from UTC (blank = now)")
        self._eff_to = QLineEdit()
        self._eff_to.setPlaceholderText("Effective to UTC (blank = open)")
        time_row.addWidget(self._eff_from)
        time_row.addWidget(self._eff_to)
        lay.addLayout(time_row)

        class_row = QHBoxLayout()
        class_row.addWidget(QLabel("Visibility:"))
        self._visibility = QComboBox()
        for level in config.VISIBILITY_LEVELS:
            self._visibility.addItem(level, level)
        self._visibility.setCurrentText(config.DEFAULT_VISIBILITY)
        class_row.addWidget(self._visibility)
        self._simulate = QCheckBox("Include in simulations")
        self._simulate.setChecked(config.DEFAULT_SIMULATE)
        class_row.addWidget(self._simulate)
        lay.addLayout(class_row)

        hint = QLabel("Pick a tactic, then Polygon or Line. Line records a "
                      "footprint width (m). Left-click vertices, right-click / "
                      "double-click to finish, Backspace to undo, Esc to cancel.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return box

    def _on_freehand_toggle(self, _state):
        if self._capture_tool is not None:
            self._capture_tool.set_freehand(self._freehand.isChecked())

    def _start_draw(self, tactic_type):
        if self._capture_tool is None:
            return
        # Un-check other tactic buttons.
        for key, spec in config.TACTIC_TYPES.items():
            btn = spec.get("_button")
            if btn is not None:
                btn.setChecked(key == tactic_type)
        self._current_type = tactic_type
        # Snap the Geometry toggle to this type's default category (user may then
        # override it). Block signals so this doesn't fire _on_geom_mode_changed
        # before the tool is armed below.
        default_mode = config.TACTIC_TYPES[tactic_type]["category"]
        self._geom_mode.blockSignals(True)
        idx = self._geom_mode.findData(default_mode)
        if idx >= 0:
            self._geom_mode.setCurrentIndex(idx)
        self._geom_mode.blockSignals(False)
        self._sync_width_enabled()

        is_area = self._geom_mode.currentData() == "area"
        self._capture_tool.set_tactic(tactic_type, is_area=is_area)
        self._capture_tool.set_freehand(self._freehand.isChecked())
        self._canvas.setMapTool(self._capture_tool)

    def _on_geom_mode_changed(self, _idx):
        """Re-arm the active capture tool when the Geometry toggle changes."""
        self._sync_width_enabled()
        if self._capture_tool is None or getattr(self, "_current_type", None) is None:
            return
        is_area = self._geom_mode.currentData() == "area"
        self._capture_tool.set_tactic(self._current_type, is_area=is_area)
        self._capture_tool.set_freehand(self._freehand.isChecked())

    def _sync_width_enabled(self):
        """Enable the width field only in Line mode; pre-fill a default width."""
        is_line = self._geom_mode.currentData() == "line"
        self._line_width.setEnabled(is_line)
        if is_line and not self._line_width.text().strip():
            self._line_width.setText(str(config.DEFAULT_LINE_WIDTH_M))

    def _on_captured(self, geometry, tactic_type):
        s = load_settings()
        src_crs = self._canvas.mapSettings().destinationCrs()
        mode = self._geom_mode.currentData()
        width = None
        if mode == "line":
            width = self._parse_width()
            if width is None:
                self._iface.messageBar().pushWarning(
                    "Paladin", "Enter a positive line width (m) before drawing "
                    "a line tactic.")
                return
        tid = self._store.add_tactic(
            tactic_type, geometry, src_crs,
            notes=self._notes.text().strip(),
            effective_from=self._eff_from.text().strip(),
            effective_to=self._eff_to.text().strip(),
            org_id=s.get("org_id", ""),
            author=s.get("author", ""),
            visibility=self._visibility.currentData(),
            simulate=self._simulate.isChecked(),
            source="drawn",
            geometry_mode=mode,
            line_width_m=width,
        )
        if tid:
            suffix = (" (%.3g m wide)" % width) if width is not None else ""
            self._iface.messageBar().pushInfo(
                "Paladin", "Added %s%s"
                % (config.TACTIC_TYPES[tactic_type]["label"], suffix))
            self.refresh_list()

    def _parse_width(self):
        """Return a positive float width in metres, or None if invalid/blank."""
        raw = self._line_width.text().strip().replace(",", ".")
        if not raw:
            return None
        try:
            val = float(raw)
        except ValueError:
            return None
        return val if val > 0 else None

    # ---- Import ------------------------------------------------------------ #
    def _build_import_group(self):
        box = QGroupBox("Import file")
        lay = QVBoxLayout(box)

        row = QHBoxLayout()
        row.addWidget(QLabel("Represents:"))
        self._import_type = QComboBox()
        for key, spec in config.TACTIC_TYPES.items():
            self._import_type.addItem(spec["label"], key)
        row.addWidget(self._import_type)
        lay.addLayout(row)

        btn = QPushButton("Import GeoJSON / KML / KMZ...")
        btn.clicked.connect(self._on_import)
        lay.addWidget(btn)
        return box

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import tactic file", "",
            "Vector (*.geojson *.json *.kml *.kmz);;All files (*)")
        if not path:
            return
        tactic_type = self._import_type.currentData()
        s = load_settings()
        n = layers.import_tactics_from_file(
            path, tactic_type, self._store,
            org_id=s.get("org_id", ""), author=s.get("author", ""),
            visibility=self._visibility.currentData(),
            simulate=self._simulate.isChecked())
        self._iface.messageBar().pushInfo("Paladin", "Imported %d feature(s)" % n)
        self.refresh_list()

    # ---- Tactic list ------------------------------------------------------- #
    def _build_list_group(self):
        box = QGroupBox("Tactics")
        lay = QVBoxLayout(box)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._zoom_to_item)
        lay.addWidget(self._list)

        row = QHBoxLayout()
        self._edit_btn = QPushButton("Edit")
        self._edit_btn.clicked.connect(self._toggle_edit)
        zoom = QPushButton("Zoom")
        zoom.clicked.connect(self._zoom_selected)
        remove = QPushButton("Delete")
        remove.clicked.connect(self._delete_selected)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_list)
        for b in (self._edit_btn, zoom, remove, refresh):
            row.addWidget(b)
        lay.addLayout(row)
        return box

    def _toggle_edit(self):
        """Hand the selected tactic to QGIS's Vertex Tool for manual editing.

        First click: activate the tactic's layer, start editing, select the
        feature, switch to the Vertex Tool -> drag/stretch/add/remove vertices.
        Second click: commit. The geometry change is picked up by the content
        hash, so the next Sync writes a new version in the same lineage.
        """
        if self._editing_lineage is None:
            tactic_id = self._selected_tactic_id()
            if not tactic_id:
                self._iface.messageBar().pushInfo(
                    "Paladin", "Select a tactic in the list first.")
                return
            layer, feat = self._store._find(tactic_id)
            if feat is None:
                return
            self._editing_lineage = tactic_id
            self._editing_layer = layer
            self._iface.setActiveLayer(layer)
            if not layer.isEditable():
                layer.startEditing()
            layer.removeSelection()
            layer.selectByIds([feat.id()])
            self._zoom_to_id(tactic_id)
            self._iface.actionVertexTool().trigger()
            self._edit_btn.setText("Finish edit")
            self._iface.messageBar().pushInfo(
                "Paladin", "Drag vertices to reshape. Click 'Finish edit' to save.")
        else:
            layer = self._editing_layer
            if layer is not None and layer.isEditable():
                layer.commitChanges()
                layer.removeSelection()
            self._editing_lineage = None
            self._editing_layer = None
            self._edit_btn.setText("Edit")
            self._iface.actionPan().trigger()
            self.refresh_list()
            self._iface.messageBar().pushInfo(
                "Paladin", "Saved. Sync to record the new version.")

    def refresh_list(self):
        self._list.clear()
        for _layer, feat in self._store.all_tactics():
            label = config.TACTIC_TYPES.get(
                feat["tactic_type"], {}).get("label", feat["tactic_type"])
            vis = feat["visibility"] or config.DEFAULT_VISIBILITY
            sim = "sim" if (feat["simulate"] or "true") == "true" else "no-sim"
            state = self._store.get_sync_state(feat["tactic_id"])
            if feat["status"] == config.STATUS_INACTIVE:
                ver = "DELETED (pending sync)"
            elif state is None:
                ver = "unsynced"
            else:
                ver = "v%d" % state["version"]
            width = feat["line_width_m"]
            geom = "line %.3gm" % width if width not in (None, "") else "polygon"
            text = "%s  |  %s  |  %s / %s  |  %s" % (label, geom, vis, sim, ver)
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, feat["tactic_id"])
            self._list.addItem(item)

    def _selected_tactic_id(self):
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _feature_for_id(self, tactic_id):
        for layer, feat in self._store.all_tactics():
            if feat["tactic_id"] == tactic_id:
                return layer, feat
        return None, None

    def _zoom_selected(self):
        self._zoom_to_id(self._selected_tactic_id())

    def _zoom_to_item(self, item):
        self._zoom_to_id(item.data(Qt.ItemDataRole.UserRole))

    def _zoom_to_id(self, tactic_id):
        if not tactic_id:
            return
        layer, feat = self._feature_for_id(tactic_id)
        if feat is None:
            return
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            return
        # Geometry is EPSG:4326; reproject the bbox into the canvas CRS.
        box_4326 = geom.boundingBox()
        dst = self._canvas.mapSettings().destinationCrs()
        rect = tactics.transform_geometry(
            tactics.QgsGeometry.fromRect(box_4326),
            "EPSG:4326", dst).boundingBox()
        rect.scale(1.4)
        self._canvas.setExtent(rect)
        self._canvas.refresh()

    def _delete_selected(self):
        tactic_id = self._selected_tactic_id()
        if not tactic_id:
            return
        # Never synced -> just drop it. Synced -> soft-delete (a tombstone
        # version is written on the next Sync; history is preserved).
        if self._store.get_sync_state(tactic_id) is None:
            self._store.remove_tactic(tactic_id)
        else:
            self._store.mark_inactive(tactic_id)
            self._iface.messageBar().pushInfo(
                "Paladin", "Marked inactive. Sync to record the deletion.")
        self.refresh_list()

    # ---- Sync -------------------------------------------------------------- #
    def _build_sync_group(self):
        box = QGroupBox("Sync")
        lay = QVBoxLayout(box)
        self._sync_btn = QPushButton("Sync to Paladin")
        self._sync_btn.clicked.connect(self._on_sync)
        lay.addWidget(self._sync_btn)
        self._sync_status = QLabel("")
        self._sync_status.setWordWrap(True)
        lay.addWidget(self._sync_status)
        return box

    def _on_sync(self):
        s = load_settings()
        author = s.get("author", "").strip()
        if not author:
            QMessageBox.warning(self, "Paladin",
                                "Set an Author / user id in Settings first.")
            return

        # Build immutable version objects. Never overwrite: create -> v1,
        # edit -> v+1 (supersedes prev), delete -> v+1 status=inactive.
        # Key: {prefix}/{tactic_id}/{version:06d}-{version_id}.geojson
        prefix = s["disturbance_prefix"].strip("/")
        now = tactics.utc_now_iso()
        uploads = []
        self._pending_removals = []   # tombstoned lineages to drop after success

        for _layer, feat in list(self._store.all_tactics()):
            lineage = feat["tactic_id"]
            state = self._store.get_sync_state(lineage)
            chash = tactics.content_hash(feat)
            status = feat["status"] or config.STATUS_ACTIVE

            if status == config.STATUS_INACTIVE:
                if state is None:
                    continue  # deleted before ever syncing -> nothing in S3
                version, op = state["version"] + 1, "delete"
            elif state is None:
                version, op = 1, "create"
            elif chash != state["hash"]:
                version, op = state["version"] + 1, "edit"
            else:
                continue  # unchanged since last sync

            version_id = str(uuid.uuid4())
            supersedes = state["version_id"] if state else ""

            # Stamp version fields onto the feature, then serialize.
            self._store.set_version_fields(
                lineage, version_id=version_id, version=version,
                supersedes=supersedes, status=status, created_utc=now,
                author=author)
            _l, feat2 = self._store._find(lineage)
            payload = tactics.feature_to_geojson(feat2 if feat2 else feat)
            obj = {"type": "FeatureCollection", "features": [payload]}
            key = ("%s/%s/%06d-%s.geojson" % (prefix, lineage, version, version_id)
                   if prefix else
                   "%s/%06d-%s.geojson" % (lineage, version, version_id))
            uploads.append((key, obj))

            self._store.set_sync_state(lineage, version, version_id, chash)
            if op == "delete":
                self._pending_removals.append(lineage)

        if not uploads:
            self._sync_status.setText("No changes to upload. Reading down...")

        self._sync_btn.setEnabled(False)
        have = self._store.current_versions()
        self._worker = SyncWorker(
            s, uploads, (prefix + "/") if prefix else "",
            have=have,
            my_org=s.get("org_id", ""), my_author=s.get("author", ""))
        self._worker.progress.connect(self._sync_status.setText)
        self._worker.downloaded.connect(self._on_downloaded)
        self._worker.finished_ok.connect(self._on_sync_done)
        self._worker.failed.connect(self._on_sync_failed)
        self._worker.start()

    def _on_downloaded(self, obj):
        # Runs on the main thread (queued signal) -> safe to mutate QGIS layers.
        # Ingests the tactic into the editable store so it shows in the list and
        # can be edited; edits continue its lineage on the next sync.
        self._store.reconcile_downloaded(obj)

    def _on_sync_done(self, up, down):
        self._sync_btn.setEnabled(True)
        # Now that the tombstone versions are written, drop the soft-deleted
        # tactics from the local canvas (their history remains in S3).
        for lineage in getattr(self, "_pending_removals", []):
            self._store.remove_tactic(lineage)
        self._pending_removals = []
        self.refresh_list()
        self._sync_status.setText("Synced. Uploaded %d, read down %d." % (up, down))
        self._iface.messageBar().pushSuccess("Paladin", "Sync complete.")

    def _on_sync_failed(self, msg):
        self._sync_btn.setEnabled(True)
        self._sync_status.setText("Sync failed: %s" % msg)
        self._iface.messageBar().pushWarning("Paladin", "Sync failed. See status.")

    # ---- Settings ---------------------------------------------------------- #
    def _build_settings_group(self):
        box = QGroupBox("Settings")
        lay = QGridLayout(box)
        s = load_settings()

        # Only per-user values are exposed. Bucket, region, prefix, backend,
        # session token, and API base/token are fixed defaults in config.py --
        # one bucket, one region, one prefix, boto3 -- so customers don't touch
        # them. The individual credentials you email are the only S3 inputs.
        self._set_author = QLineEdit(s["author"])
        self._set_org = QLineEdit(s["org_id"])
        self._set_akey = QLineEdit(s["access_key"])
        self._set_skey = QLineEdit(s["secret_key"])
        self._set_skey.setEchoMode(QLineEdit.EchoMode.Password)

        rows = [
            ("Author / user id", self._set_author),
            ("Org id", self._set_org),
            ("AWS Access Key ID", self._set_akey),
            ("AWS Secret Access Key", self._set_skey),
        ]
        for i, (label, widget) in enumerate(rows):
            lay.addWidget(QLabel(label), i, 0)
            lay.addWidget(widget, i, 1)

        hint = QLabel("Author = you (individual, for private tactics). Org id = "
                      "your organization (controls 'org' visibility). Paste the "
                      "two AWS keys you were emailed.")
        hint.setWordWrap(True)
        lay.addWidget(hint, len(rows), 0, 1, 2)

        save = QPushButton("Save settings")
        save.clicked.connect(self._save_settings)
        lay.addWidget(save, len(rows) + 1, 0, 1, 2)
        return box

    def _save_settings(self):
        save_settings({
            "author": self._set_author.text().strip(),
            "org_id": self._set_org.text().strip(),
            "access_key": self._set_akey.text().strip(),
            "secret_key": self._set_skey.text().strip(),
        })
        self._iface.messageBar().pushInfo("Paladin", "Settings saved.")
