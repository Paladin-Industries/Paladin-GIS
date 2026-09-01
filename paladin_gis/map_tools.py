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
"""Canvas capture tools for drawing tactics.

One tool, `TacticCaptureTool`, handles both polygon and line capture and both
interaction styles:

  * Vertex mode (default): left-click adds a vertex, mouse-move previews the
    next segment, right-click or double-click finishes, Backspace undoes the
    last vertex, Esc cancels.
  * Freehand mode ("free polygon"): press and drag to stream vertices, release
    to finish. Toggle via `set_freehand(True)`.

On finish it emits `captured(QgsGeometry, str)` -> (geometry in canvas CRS,
tactic_type). The panel turns that into a stored tactic.
"""

import math

from qgis.core import Qgis, QgsGeometry, QgsPointXY
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor

from . import config

_FREEHAND_MIN_PX = 3  # min screen-pixel step between streamed freehand vertices


class TacticCaptureTool(QgsMapTool):
    captured = pyqtSignal(QgsGeometry, str)
    canceled = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._tactic_type = None
        self._is_area = True
        self._color = QColor(255, 176, 0, 200)   # default until set_tactic arms it
        self._freehand = False

        self._points = []            # committed vertices (QgsPointXY, map CRS)
        self._streaming = False      # mid freehand drag
        self._last_px = None         # last freehand device pos

        self._band = None            # committed geometry rubber band
        self._temp_band = None       # moving-segment preview band

    # -- configuration ------------------------------------------------------ #
    def set_tactic(self, tactic_type, is_area=None):
        """Arm the tool for `tactic_type`.

        `is_area` overrides the type's default `category` for this draw: pass
        True to capture a polygon, False to capture a line. When None, the
        type's default is used. Re-arm the tool whenever the Geometry toggle
        changes so the rubber band and finish rules match what will be stored.
        """
        spec = config.TACTIC_TYPES.get(tactic_type)
        if spec is None:
            return
        self._tactic_type = tactic_type
        if is_area is None:
            self._is_area = spec["category"] == "area"
        else:
            self._is_area = bool(is_area)
        self._color = QColor(*spec["color"])
        # A partially-drawn shape can't switch geometry type mid-stream; reset.
        self._clear()
        self._reset_bands()

    def set_freehand(self, enabled):
        self._freehand = bool(enabled)

    def is_ready(self):
        return self._tactic_type is not None

    # -- QgsMapTool lifecycle ---------------------------------------------- #
    def activate(self):
        super().activate()
        self._reset_bands()

    def deactivate(self):
        self._clear()
        super().deactivate()

    # -- rubber bands ------------------------------------------------------- #
    def _reset_bands(self):
        self._clear_bands()
        gtype = (Qgis.GeometryType.Polygon if self._is_area
                 else Qgis.GeometryType.Line)
        self._band = QgsRubberBand(self._canvas, gtype)
        self._temp_band = QgsRubberBand(self._canvas, gtype)
        for band, width, alpha in ((self._band, 2, 60), (self._temp_band, 2, 30)):
            c = QColor(self._color)
            band.setColor(c)
            fill = QColor(self._color)
            fill.setAlpha(alpha)
            band.setFillColor(fill)
            band.setWidth(width)

    def _clear_bands(self):
        for attr in ("_band", "_temp_band"):
            band = getattr(self, attr, None)
            if band is not None:
                self._canvas.scene().removeItem(band)
                setattr(self, attr, None)

    def _clear(self):
        self._points = []
        self._streaming = False
        self._last_px = None
        self._clear_bands()

    # -- redraw ------------------------------------------------------------- #
    def _redraw_committed(self):
        if self._band is None:
            return
        self._band.reset(Qgis.GeometryType.Polygon if self._is_area
                         else Qgis.GeometryType.Line)
        for pt in self._points:
            self._band.addPoint(pt, False)
        self._band.updatePosition()
        self._band.show()

    def _preview_to(self, map_point):
        if self._temp_band is None or not self._points:
            return
        gtype = (Qgis.GeometryType.Polygon if self._is_area
                 else Qgis.GeometryType.Line)
        self._temp_band.reset(gtype)
        for pt in self._points:
            self._temp_band.addPoint(pt, False)
        self._temp_band.addPoint(map_point, True)
        self._temp_band.show()

    # -- events ------------------------------------------------------------- #
    def canvasPressEvent(self, event):
        if not self.is_ready():
            return
        map_pt = self.toMapCoordinates(event.pos())

        if self._freehand and event.button() == Qt.MouseButton.LeftButton:
            # Start streaming.
            self._streaming = True
            self._points = [map_pt]
            self._last_px = event.pos()
            self._redraw_committed()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            self._points.append(map_pt)
            self._redraw_committed()
        elif event.button() == Qt.MouseButton.RightButton:
            self._finish()

    def canvasMoveEvent(self, event):
        if not self.is_ready():
            return
        map_pt = self.toMapCoordinates(event.pos())

        if self._freehand and self._streaming:
            if self._last_px is None or _px_dist(self._last_px, event.pos()) >= _FREEHAND_MIN_PX:
                self._points.append(map_pt)
                self._last_px = event.pos()
                self._redraw_committed()
            return

        # Vertex mode: preview the segment from last vertex to cursor.
        self._preview_to(map_pt)

    def canvasReleaseEvent(self, event):
        if self._freehand and self._streaming and event.button() == Qt.MouseButton.LeftButton:
            self._streaming = False
            self._finish()

    def canvasDoubleClickEvent(self, event):
        if not self._freehand:
            # Double-click adds no duplicate vertex; just finish.
            self._finish()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._clear()
            self._reset_bands()
            self.canceled.emit()
        elif event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            if self._points:
                self._points.pop()
                self._redraw_committed()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish()

    # -- completion --------------------------------------------------------- #
    def _finish(self):
        min_pts = 3 if self._is_area else 2
        if len(self._points) < min_pts:
            self._clear()
            self._reset_bands()
            self.canceled.emit()
            return

        if self._is_area:
            ring = list(self._points)
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            geom = QgsGeometry.fromPolygonXY([ring])
        else:
            geom = QgsGeometry.fromPolylineXY(list(self._points))

        tactic_type = self._tactic_type
        self._clear()
        self._reset_bands()
        if geom is not None and not geom.isEmpty():
            self.captured.emit(geom, tactic_type)


def _px_dist(a, b):
    return math.hypot(a.x() - b.x(), a.y() - b.y())
