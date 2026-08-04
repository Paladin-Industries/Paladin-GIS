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
"""Hardcoded LANDFIRE 2025 fuels catalog.

ArcGIS ImageServer endpoints from the LANDFIRE Product Service (LFPS), LF2025
release, CONUS. Naming convention:

    https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2025/LF2025_{PRODUCT}_CONUS/ImageServer

    e.g.  .../Landfire_LF2025/LF2025_FBFM40_CONUS/ImageServer   (confirmed)

Kept deliberately simple: one year, one region, the core fuelbed layers. To add
a product, append one row below. To add a year/region later, this whole file is
the thing that grows. `verified=True` means the exact endpoint was confirmed;
the rest follow the convention — run `python -m paladin_gis.landfire_catalog` to
ping them all.
"""

LFPS_ROOT = "https://lfps.usgs.gov/arcgis/rest/services"
_YEAR = "2025"
_REGION = "CONUS"


def _svc(product):
    return "{root}/Landfire_LF{y}/LF{y}_{p}_{r}/ImageServer".format(
        root=LFPS_ROOT, y=_YEAR, p=product, r=_REGION)


# id / theme / label / url / provider / verified
LANDFIRE_CATALOG = [
    # -- Surface fuel ------------------------------------------------------- #
    dict(id="fbfm40", theme="Surface Fuel",
         label="Scott & Burgan 40 Fuel Models (FBFM40)",
         url=_svc("FBFM40"), provider="arcgismapserver", verified=True),
    dict(id="fbfm13", theme="Surface Fuel",
         label="Anderson 13 Fuel Models (FBFM13)",
         url=_svc("FBFM13"), provider="arcgismapserver", verified=False),

    # -- Canopy fuel -------------------------------------------------------- #
    dict(id="cc", theme="Canopy Fuel",
         label="Canopy Cover (CC)",
         url=_svc("CC"), provider="arcgismapserver", verified=False),
    dict(id="ch", theme="Canopy Fuel",
         label="Canopy Height (CH)",
         url=_svc("CH"), provider="arcgismapserver", verified=False),
    dict(id="cbh", theme="Canopy Fuel",
         label="Canopy Base Height (CBH)",
         url=_svc("CBH"), provider="arcgismapserver", verified=False),
    dict(id="cbd", theme="Canopy Fuel",
         label="Canopy Bulk Density (CBD)",
         url=_svc("CBD"), provider="arcgismapserver", verified=False),
]


def themes():
    seen, out = set(), []
    for e in LANDFIRE_CATALOG:
        if e["theme"] not in seen:
            seen.add(e["theme"])
            out.append(e["theme"])
    return out


def entries_for_theme(theme):
    return [e for e in LANDFIRE_CATALOG if e["theme"] == theme]


def entry_by_id(entry_id):
    for e in LANDFIRE_CATALOG:
        if e["id"] == entry_id:
            return e
    return None


def _check_all():
    """Ping every endpoint. Run outside QGIS:  python -m paladin_gis.landfire_catalog"""
    import json
    import urllib.error
    import urllib.request

    for e in LANDFIRE_CATALOG:
        url = e["url"] + "?f=json"
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                meta = json.load(resp)
            ok = "error" not in meta
            note = meta.get("name", "") if ok else meta["error"].get("message", "")
        except (urllib.error.URLError, ValueError, TimeoutError) as exc:
            ok, note = False, str(exc)
        print("%s  %-7s %s  %s" % ("OK " if ok else "FAIL", e["id"], e["url"], note))


if __name__ == "__main__":
    _check_all()
