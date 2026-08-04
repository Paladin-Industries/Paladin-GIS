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
"""Shared Paladin wire contract — pure standard library, no GIS imports.

This package is the single definition of what a tactic IS on the wire, so the
QGIS plugin, this ArcGIS Pro toolbox, and any server-side consumer agree
byte-for-byte. It mirrors the QGIS plugin's config.py / tactics.py / s3_client.py
with the QGIS-specific parts removed.

If you change something here, change it in the QGIS plugin too.
"""
