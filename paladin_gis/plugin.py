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
"""Main Paladin plugin class."""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .map_tools import TacticCaptureTool
from .panel import PaladinDock
from .tactics import TacticStore

_ICON = os.path.join(os.path.dirname(__file__), "resources", "icon.svg")


class PaladinPlugin:
    def __init__(self, iface):
        self._iface = iface
        self._action = None
        self._dock = None
        self._store = None
        self._capture_tool = None

    # -- QGIS hooks --------------------------------------------------------- #
    def initGui(self):  # noqa: N802
        icon = QIcon(_ICON)
        self._action = QAction(icon, "Paladin", self._iface.mainWindow())
        self._action.setCheckable(True)
        self._action.triggered.connect(self._toggle)
        self._iface.addToolBarIcon(self._action)
        self._iface.addPluginToMenu("Paladin", self._action)

    def unload(self):
        if self._capture_tool is not None:
            self._capture_tool.deactivate()
        if self._dock is not None:
            self._iface.removeDockWidget(self._dock)
            self._dock.deleteLater()
            self._dock = None
        if self._action is not None:
            self._iface.removeToolBarIcon(self._action)
            self._iface.removePluginMenu("Paladin", self._action)
            self._action = None

    # -- behavior ----------------------------------------------------------- #
    def _toggle(self, checked):
        if self._dock is None:
            self._build()
        self._dock.setVisible(checked)

    def _build(self):
        self._store = TacticStore()
        self._capture_tool = TacticCaptureTool(self._iface.mapCanvas())
        self._dock = PaladinDock(self._iface, self._store)
        self._dock.set_capture_tool(self._capture_tool)
        self._iface.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._dock)
        self._dock.visibilityChanged.connect(self._on_visibility)

    def _on_visibility(self, visible):
        if self._action is not None:
            self._action.setChecked(visible)
