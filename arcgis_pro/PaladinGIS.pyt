# -*- coding: utf-8 -*-
# Paladin GIS - ArcGIS Pro toolbox for wildfire tactic authoring and sync
# Copyright (C) 2026 Paladin Industries, Inc.
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE / NOTICE shipped with the QGIS plugin.
"""Paladin GIS for ArcGIS Pro.

ONE FILE. Install = Catalog pane -> Toolboxes -> Add Toolbox -> this file,
then right-click it -> Add To Favorites so it follows you into every project.

Port of the QGIS plugin (paladin_gis 0.3.0). Same wire contract
(paladin.tactic.v3, append-only versioning, mcleod keyless broker). Drawing and
editing use ArcGIS Pro's own editing tools: the tactic layers are feature
classes with one SUBTYPE per tactic type, so Pro's Create Features pane shows a
ready-made template for every tactic (Dozer Line, Handline, Rx Burn, ...).

Tools
  Sign In              - email + password once; session kept (encrypted on Windows)
  Add Tactic Layers    - put the editable tactic layers in the current map
  Sync with Paladin    - push your changes, pull everyone else's
  Import Tactics       - any layer/selection, or a GeoJSON/KML/KMZ file
  Add LANDFIRE Layers  - LF2025 fuels image services
  Sign Out
"""

import base64
import datetime
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile

import arcpy

TOOLBOX_VERSION = "0.3.0-pro.1"

# =========================================================================== #
# Deployment constants  (the ONLY things to change per release)
# =========================================================================== #
# mcleod GIS auth endpoint. Users never see this unless they open "Advanced"
# in Sign In. The disturbance broker is derived from it (.../gis/disturbances).
AUTH_ENDPOINT = "https://gis.paladinindustries.com/gis/auth"
DISTURBANCE_PREFIX = "disturbances"

# =========================================================================== #
# Wire contract  (mirrors paladin_gis/config.py - keep in lockstep)
# =========================================================================== #
TACTIC_SCHEMA = "paladin.tactic.v3"
WGS84 = "EPSG:4326"
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
VISIBILITY_LEVELS = ["private", "org", "public"]
DEFAULT_VISIBILITY = "private"
DEFAULT_LINE_WIDTH_M = 3.0

# code = subtype code in the geodatabase (stable; never renumber).
TACTIC_TYPES = {
    "fuel_break":      dict(code=1, label="Fuel Break",      category="area", model_role="fuel_modification", color=(255, 176, 0, 35)),
    "prescribed_burn": dict(code=2, label="Prescribed Burn", category="area", model_role="fuel_modification", color=(214, 69, 65, 35)),
    "scratch_line":    dict(code=3, label="Scratch Line",    category="line", model_role="control_line",      color=(255, 214, 0, 90)),
    "handline":        dict(code=4, label="Handline",        category="line", model_role="control_line",      color=(0, 200, 120, 90)),
    "dozer_line":      dict(code=5, label="Dozer Line",      category="line", model_role="control_line",      color=(120, 90, 60, 90)),
    "hose_lay":        dict(code=6, label="Hose Lay",        category="line", model_role="control_line",      color=(0, 150, 220, 90)),
    "retardant":       dict(code=7, label="Retardant",       category="area", model_role="fuel_modification", color=(233, 30, 99, 45)),
}
CODE_TO_TYPE = {s["code"]: k for k, s in TACTIC_TYPES.items()}
LABEL_TO_TYPE = {s["label"]: k for k, s in TACTIC_TYPES.items()}

# Wire property order == QGIS config.TACTIC_FIELDS.
WIRE_FIELDS = [
    "tactic_id", "version_id", "version", "supersedes", "status", "tactic_type",
    "model_role", "label", "notes", "created_utc", "effective_from_utc",
    "effective_to_utc", "org_id", "author", "visibility", "simulate", "source",
    "source_file", "line_width_m",
]
# Fields whose change is a "meaningful edit" (new version on next sync).
HASH_FIELDS = ["tactic_type", "label", "notes", "effective_from_utc",
               "effective_to_utc", "org_id", "visibility", "simulate",
               "line_width_m"]

LANDFIRE_ROOT = "https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2025"
LANDFIRE_CATALOG = [
    ("Surface Fuel: Scott & Burgan 40 Fuel Models (FBFM40)", "FBFM40"),
    ("Surface Fuel: Anderson 13 Fuel Models (FBFM13)", "FBFM13"),
    ("Canopy Fuel: Canopy Cover (CC)", "CC"),
    ("Canopy Fuel: Canopy Height (CH)", "CH"),
    ("Canopy Fuel: Canopy Base Height (CBH)", "CBH"),
    ("Canopy Fuel: Canopy Bulk Density (CBD)", "CBD"),
]


def landfire_url(product):
    return "%s/LF2025_%s_CONUS/ImageServer" % (LANDFIRE_ROOT, product)


class PaladinError(Exception):
    """User-facing error: message is shown as-is in the tool's messages."""


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# =========================================================================== #
# Value normalization (shared by gdb read/write, payloads and hashing)
# =========================================================================== #
_ISO_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%MZ",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%d")


def parse_utc(value):
    """str|datetime|None -> naive UTC datetime or None. Lenient on input."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return value.replace(microsecond=0)
    s = str(value).strip()
    if not s:
        return None
    m = re.match(r"^(.*?)(\.\d+)?([+-]\d\d:?\d\d)$", s)
    if m and "T" in s:                      # explicit offset -> convert to UTC
        try:
            dt = datetime.datetime.fromisoformat(s)
            return dt.astimezone(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)
        except ValueError:
            pass
    s = re.sub(r"\.\d+(?=Z?$)", "", s)       # drop fractional seconds
    for fmt in _ISO_FORMATS:
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def fmt_utc(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def fmt_width(value):
    if value in (None, ""):
        return ""
    try:
        v = float(str(value).replace(",", "."))
    except ValueError:
        return ""
    return str(v) if v > 0 else ""


def norm_simulate(value):
    s = str(value).strip().lower() if value is not None else ""
    if s in ("false", "0", "no", "n", "exclude"):
        return "false"
    return "true"


def norm_visibility(value):
    s = str(value or "").strip().lower()
    return s if s in VISIBILITY_LEVELS else DEFAULT_VISIBILITY


# =========================================================================== #
# GeoJSON geometry helpers (pure Python)
# =========================================================================== #
def geom_family(gj):
    t = (gj or {}).get("type", "")
    if t in ("Polygon", "MultiPolygon"):
        return "area"
    if t in ("LineString", "MultiLineString"):
        return "line"
    return None


def to_multi(gj):
    """Normalize to Multi* (the QGIS plugin always ships Multi*)."""
    t = gj.get("type")
    c = gj.get("coordinates")
    if t == "Polygon":
        return {"type": "MultiPolygon", "coordinates": [c]}
    if t == "LineString":
        return {"type": "MultiLineString", "coordinates": [c]}
    return {"type": t, "coordinates": c}


def strip_z(gj):
    """Drop Z/M and round to 1e-9 deg (well under the gdb's xy resolution)."""
    def walk(c):
        if c and isinstance(c[0], (int, float)):
            return [round(float(c[0]), 9), round(float(c[1]), 9)]
        return [walk(x) for x in c]
    return {"type": gj["type"], "coordinates": walk(gj["coordinates"])}


def _flat_coords(c, out):
    if c and isinstance(c[0], (int, float)):
        out.append((round(c[0], 7), round(c[1], 7)))
    else:
        for x in c:
            _flat_coords(x, out)
    return out


def content_hash(props, gj):
    """Stable fingerprint of what makes a tactic 'changed'.

    Geometry contributes as a SORTED multiset of 1e-7-rounded vertices, so ring
    orientation / start vertex (which ArcGIS normalizes on write) never counts
    as an edit, while moving, adding or removing a vertex always does.
    """
    parts = [geom_family(gj) or ""]
    parts.append(";".join("%r,%r" % xy for xy in sorted(_flat_coords(gj.get("coordinates") or [], []))))
    for name in HASH_FIELDS:
        parts.append(str(props.get(name, "") or ""))
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def first_feature(obj):
    if not isinstance(obj, dict):
        return None
    if obj.get("type") == "FeatureCollection":
        feats = obj.get("features") or []
        return feats[0] if feats else None
    if obj.get("type") == "Feature":
        return obj
    return None


# =========================================================================== #
# Session  (%APPDATA%/PaladinGIS/session.json, DPAPI-encrypted on Windows)
# =========================================================================== #
def _session_dir():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "PaladinGIS")


def _session_path():
    return os.environ.get("PALADIN_SESSION_FILE") or os.path.join(_session_dir(), "session.json")


def _dpapi(data, protect):
    """Windows DPAPI (current user). Raises on any failure / non-Windows."""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def load_session():
    try:
        with open(_session_path(), "r", encoding="utf-8") as fh:
            wrapper = json.load(fh)
    except (OSError, ValueError):
        return {}
    try:
        if "dpapi" in wrapper:
            raw = _dpapi(base64.b64decode(wrapper["dpapi"]), protect=False)
            return json.loads(raw.decode("utf-8"))
        return dict(wrapper.get("plain") or {})
    except Exception:  # noqa: BLE001 - unreadable/other user's blob -> signed out
        return {"email": wrapper.get("email", "")}


def save_session(data):
    os.makedirs(os.path.dirname(_session_path()), exist_ok=True)
    raw = json.dumps(data).encode("utf-8")
    try:
        wrapper = {"email": data.get("email", ""),
                   "dpapi": base64.b64encode(_dpapi(raw, protect=True)).decode("ascii")}
    except Exception:  # noqa: BLE001 - non-Windows: plaintext, like QgsSettings
        wrapper = {"email": data.get("email", ""), "plain": data}
    tmp = _session_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(wrapper, fh)
    os.replace(tmp, _session_path())


def signed_in(session):
    return bool((session or {}).get("refresh_token"))


# =========================================================================== #
# mcleod client  (auth + keyless disturbance broker)
# =========================================================================== #
_AUTH_FRIENDLY = {
    400: "Missing email or password.",
    401: "Login failed - check your email and password.",
    403: "Your Paladin account isn't licensed for the GIS tool. Contact sales@paladinindustries.com.",
    429: "Too many attempts. Wait a minute and try again.",
    500: "The login service had an error. Try again shortly.",
}


class HttpError(Exception):
    def __init__(self, status, body=""):
        super().__init__("HTTP %s" % status)
        self.status = status
        self.body = body


def http_json(method, url, body=None, headers=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"Accept": "application/json",
         "User-Agent": "PaladinGIS-ArcGISPro/%s" % TOOLBOX_VERSION}
    if body is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        raise HttpError(exc.code, text)
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise PaladinError("Couldn't reach Paladin (%s). Check your internet connection." % reason)
    return json.loads(raw) if raw else None


def auth_call(endpoint, path, payload):
    try:
        return http_json("POST", endpoint.rstrip("/") + path, payload, timeout=20) or {}
    except HttpError as exc:
        msg, is_json = "", False
        try:
            msg = (json.loads(exc.body) or {}).get("message", "")
            is_json = True
        except (ValueError, AttributeError):
            pass
        if not is_json and exc.status in (403, 503):
            raise PaladinError("Paladin's login service was blocked by network security "
                               "(HTTP %s). Contact support@paladinindustries.com." % exc.status)
        raise PaladinError(msg or _AUTH_FRIENDLY.get(exc.status, "Login failed (HTTP %s)." % exc.status))


def broker_base(endpoint):
    base = endpoint.rstrip("/")
    if base.endswith("/gis/auth"):
        return base[: -len("/auth")] + "/disturbances"
    return base + "/disturbances"


class McleodBackend:
    """Same list/get/put contract as the QGIS McleodBackend."""

    def __init__(self, session):
        self.session = session
        # Stored only when overridden, so a release can move the default
        # without stranding signed-in users.
        self.endpoint = session.get("endpoint") or AUTH_ENDPOINT
        self.base = broker_base(self.endpoint)

    def _refresh(self):
        s = self.session
        if not (s.get("refresh_token") and s.get("email")):
            return False
        try:
            res = auth_call(self.endpoint, "/refresh",
                            {"refresh_token": s["refresh_token"], "email": s["email"]})
        except PaladinError:
            return False
        if not res.get("token"):
            return False
        s["token"] = res["token"]
        s["refresh_token"] = res.get("refresh_token", s["refresh_token"])
        save_session(s)
        return True

    def _send(self, method, url, body=None, retried=False):
        try:
            return http_json(method, url, body,
                             headers={"Authorization": "Bearer %s" % self.session.get("token", "")})
        except HttpError as exc:
            if exc.status == 401:
                if not retried and self._refresh():
                    return self._send(method, url, body, retried=True)
                raise PaladinError("Your Paladin session expired. Run 'Sign In' again.")
            raise

    def list_keys(self, prefix):
        url = self.base + "/list?" + urllib.parse.urlencode({"prefix": prefix})
        try:
            return [k for k in ((self._send("GET", url) or {}).get("keys") or [])
                    if k.endswith(".geojson")]
        except HttpError as exc:
            raise PaladinError("Couldn't list Paladin tactics (HTTP %s)." % exc.status)

    def get_geojson(self, key):
        url = self.base + "/get?" + urllib.parse.urlencode({"key": key})
        try:
            return self._send("GET", url)
        except HttpError as exc:
            if exc.status in (403, 404):
                return None                 # not visible to me / gone
            raise PaladinError("Download failed for %s (HTTP %s)." % (key, exc.status))

    def put_geojson(self, key, obj):
        """True = stored. False = version-number collision (HTTP 409)."""
        try:
            self._send("POST", self.base + "/put", body=obj)
            return True
        except HttpError as exc:
            if exc.status == 409:
                return False
            raise PaladinError("Upload failed for %s (HTTP %s)." % (key, exc.status))


# =========================================================================== #
# Remote key index
# =========================================================================== #
def parse_key(prefix, key):
    """-> (lineage, version:int, version_id) for
    {prefix}/{lineage}/{version:06d}-{version_id}.geojson, or legacy flat
    {prefix}/{lineage}.geojson (single version, v1)."""
    rel = key[len(prefix):] if prefix and key.startswith(prefix) else key
    rel = rel.lstrip("/")
    if "/" in rel:
        lineage, fname = rel.split("/", 1)
        stem = fname.rsplit("/", 1)[-1][: -len(".geojson")] if fname.endswith(".geojson") else fname
        head, _, vid = stem.partition("-")
        try:
            return lineage, int(head), vid
        except ValueError:
            return lineage, 1, vid
    lineage = rel[: -len(".geojson")] if rel.endswith(".geojson") else rel
    return lineage, 1, ""


def index_remote(prefix, keys):
    """{lineage: {"version", "version_id", "key"}} for the current version."""
    out = {}
    for k in keys:
        lineage, ver, vid = parse_key(prefix, k)
        cur = out.get(lineage)
        if cur is None or (ver, k) > (cur["version"], cur["key"]):
            out[lineage] = {"version": ver, "version_id": vid, "key": k}
    return out


def visible_to(props, author, org):
    """Client-side visibility (fail closed). mcleod enforces for real."""
    vis = (props.get("visibility") or "private").lower()
    if vis == "public":
        return True
    if vis == "org":
        return bool(org) and props.get("org_id") == org
    if vis == "private":
        return bool(author) and props.get("author") == author
    return False


# =========================================================================== #
# File readers  (GeoJSON / KML / KMZ -> [(geojson_geometry, name)], all WGS84)
# =========================================================================== #
def _geojson_crs_code(obj):
    name = (((obj or {}).get("crs") or {}).get("properties") or {}).get("name", "")
    m = re.search(r"EPSG:*:?(\d+)", name or "")
    if not m:
        return None
    code = int(m.group(1))
    return None if code == 4326 else code


def read_geojson_file(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        obj = json.load(fh)
    t = obj.get("type")
    if t == "FeatureCollection":
        feats = obj.get("features") or []
    elif t == "Feature":
        feats = [obj]
    elif t:
        feats = [{"type": "Feature", "geometry": obj, "properties": {}}]
    else:
        feats = []
    out = []
    for f in feats:
        props = f.get("properties") or {}
        name = props.get("name") or props.get("Name") or props.get("label") or ""
        for g in _explode_collection(f.get("geometry")):
            out.append((g, str(name)))
    return out, _geojson_crs_code(obj)


def _explode_collection(g):
    if not g:
        return []
    if g.get("type") == "GeometryCollection":
        res = []
        for sub in g.get("geometries") or []:
            res.extend(_explode_collection(sub))
        return res
    return [g]


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _kml_coords(text):
    pts = []
    for tok in (text or "").split():
        bits = tok.split(",")
        if len(bits) >= 2:
            try:
                pts.append([float(bits[0]), float(bits[1])])
            except ValueError:
                continue
    return pts


def _kml_geoms(el):
    tag = el.tag
    if tag == "Point":
        return [{"type": "Point", "coordinates": (_kml_coords(el.findtext("coordinates")) or [[0, 0]])[0]}]
    if tag == "LineString":
        pts = _kml_coords(el.findtext("coordinates"))
        return [{"type": "LineString", "coordinates": pts}] if len(pts) >= 2 else []
    if tag == "LinearRing":
        pts = _kml_coords(el.findtext("coordinates"))
        return [{"type": "Polygon", "coordinates": [pts]}] if len(pts) >= 4 else []
    if tag == "Polygon":
        outer = _kml_coords(el.findtext("outerBoundaryIs/LinearRing/coordinates"))
        if len(outer) < 4:
            return []
        rings = [outer] + [r for r in (_kml_coords(i.findtext("LinearRing/coordinates"))
                                       for i in el.findall("innerBoundaryIs")) if len(r) >= 4]
        return [{"type": "Polygon", "coordinates": rings}]
    if tag in ("MultiGeometry", "MultiTrack"):
        res = []
        for child in el:
            res.extend(_kml_geoms(child))
        return res
    return []


def read_kml_bytes(raw):
    root = _strip_ns(ET.fromstring(raw))
    out = []
    for pm in root.iter("Placemark"):
        name = (pm.findtext("name") or "").strip()
        for child in pm:
            for g in _kml_geoms(child):
                out.append((g, name))
    return out


def read_kml_file(path):
    if path.lower().endswith(".kmz"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".kml")]
            if not names:
                raise PaladinError("No .kml document inside %s." % os.path.basename(path))
            main = "doc.kml" if "doc.kml" in names else names[0]
            return read_kml_bytes(z.read(main)), None
    with open(path, "rb") as fh:
        return read_kml_bytes(fh.read()), None


# =========================================================================== #
# arcpy geometry bridge
# =========================================================================== #
SR_WGS84 = None


def sr_wgs84():
    global SR_WGS84
    if SR_WGS84 is None:
        SR_WGS84 = arcpy.SpatialReference(4326)
    return SR_WGS84


def arc_to_geojson(geom):
    """arcpy geometry (any SR) -> WGS84 GeoJSON Multi* geometry, or None."""
    if geom is None:
        return None
    try:
        if geom.isMultipart is None:  # pragma: no cover - defensive
            return None
    except AttributeError:
        pass
    sr = getattr(geom, "spatialReference", None)
    if sr is not None and getattr(sr, "factoryCode", 4326) not in (4326, 0, None):
        geom = project_to_wgs84(geom)
    if getattr(geom, "hasCurves", False):
        # True curves (arcs / Beziers from Pro editing) have no GeoJSON form.
        geom = geom.densify("ANGLE", 0.00001, 0.0174533)  # <=1 deg chord angle
    try:
        gj = geom.__geo_interface__
    except Exception:  # noqa: BLE001
        return None
    if not gj or not gj.get("coordinates"):
        return None
    if geom_family(gj) is None:
        return gj if gj.get("type") else None
    return to_multi(strip_z(gj))


def project_to_wgs84(geom):
    src = geom.spatialReference
    xform = ""
    try:
        cands = arcpy.ListTransformations(src, sr_wgs84())
        xform = cands[0] if cands else ""
    except Exception:  # noqa: BLE001
        xform = ""
    return geom.projectAs(sr_wgs84(), xform) if xform else geom.projectAs(sr_wgs84())


def _with_sr(gj, epsg):
    """Build an arcpy geometry from GeoJSON in a projected/other CRS."""
    esri = {"spatialReference": {"wkid": epsg}}
    t = gj["type"]
    c = gj["coordinates"]
    if t == "LineString":
        esri["paths"] = [c]
    elif t == "MultiLineString":
        esri["paths"] = c
    elif t == "Polygon":
        esri["rings"] = c
    elif t == "MultiPolygon":
        esri["rings"] = [r for poly in c for r in poly]
    else:
        raise PaladinError("Unsupported geometry type %s" % t)
    return arcpy.AsShape(esri, True)


# =========================================================================== #
# Geodatabase store
# =========================================================================== #
AREA_FC = "Paladin_Tactics_Area"
LINE_FC = "Paladin_Tactics_Line"
STATE_TABLE = "Paladin_SyncState"
LAYER_NAMES = {"area": "Paladin Tactics (Areas)", "line": "Paladin Tactics (Lines)"}

# name, type, length, alias, domain
GDB_FIELDS = [
    ("tactic_code", "SHORT", None, "Tactic", None),
    ("label", "TEXT", 255, "Name", None),
    ("notes", "TEXT", 2048, "Notes", None),
    ("visibility", "TEXT", 16, "Visibility", "Paladin_Visibility"),
    ("simulate", "TEXT", 8, "Include in Simulations", "Paladin_Simulate"),
    ("line_width_m", "DOUBLE", None, "Line Width (m)", None),
    ("effective_from_utc", "DATE", None, "Effective From (UTC)", None),
    ("effective_to_utc", "DATE", None, "Effective To (UTC)", None),
    ("status", "TEXT", 16, "Status", "Paladin_Status"),
    ("tactic_type", "TEXT", 32, "System: Tactic Type", None),
    ("model_role", "TEXT", 32, "System: Model Role", None),
    ("tactic_id", "TEXT", 64, "System: Tactic ID", None),
    ("version", "TEXT", 16, "System: Version", None),
    ("version_id", "TEXT", 64, "System: Version ID", None),
    ("supersedes", "TEXT", 64, "System: Supersedes", None),
    ("created_utc", "TEXT", 32, "System: Version Created (UTC)", None),
    ("org_id", "TEXT", 128, "System: Org", None),
    ("author", "TEXT", 255, "System: Author", None),
    ("source", "TEXT", 16, "System: Source", None),
    ("source_file", "TEXT", 255, "System: Source File", None),
]
TEXT_LEN = {n: ln for n, t, ln, _a, _d in GDB_FIELDS if t == "TEXT"}
DOMAINS = {
    "Paladin_Visibility": ("Who can see a tactic", [("private", "Private (only me)"), ("org", "My organization"), ("public", "Everyone")]),
    "Paladin_Simulate": ("Include in simulations", [("true", "Include"), ("false", "Exclude (proposed only)")]),
    "Paladin_Status": ("Tactic status", [("active", "Active"), ("inactive", "Deleted (sync to publish)")]),
}
FIELD_DEFAULTS = {"visibility": DEFAULT_VISIBILITY, "simulate": "true", "status": STATUS_ACTIVE}
CURSOR_FIELDS = [f[0] for f in GDB_FIELDS] + ["OID@", "SHAPE@"]
_DATE_FIELDS = {"effective_from_utc", "effective_to_utc"}


class GdbStore:
    """Two feature classes (Area/Line) + a sync-state table in one gdb.

    Sync state lives INSIDE the gdb on purpose: deleting/replacing the gdb
    deletes its state too, so a fresh gdb can never be mistaken for "the user
    deleted every tactic" (which would tombstone everything upstream).
    """

    def __init__(self, gdb):
        self.gdb = gdb
        self.fc = {"area": os.path.join(gdb, AREA_FC), "line": os.path.join(gdb, LINE_FC)}
        self.state_table = os.path.join(gdb, STATE_TABLE)

    # -- schema ------------------------------------------------------------ #
    def ensure(self):
        """Create whatever is missing. Returns the set of kinds created now."""
        created = set()
        if not arcpy.Exists(self.gdb):
            folder, name = os.path.split(self.gdb)
            os.makedirs(folder, exist_ok=True)
            arcpy.management.CreateFileGDB(folder, name)
        self._ensure_domains()
        for kind, path in self.fc.items():
            if not arcpy.Exists(path):
                self._create_fc(kind)
                created.add(kind)
        if not arcpy.Exists(self.state_table):
            arcpy.management.CreateTable(self.gdb, STATE_TABLE)
            for name, ftype, ln in (("tactic_id", "TEXT", 64), ("kind", "TEXT", 8),
                                    ("version", "LONG", None), ("version_id", "TEXT", 64),
                                    ("hash", "TEXT", 64)):
                arcpy.management.AddField(self.state_table, name, ftype, field_length=ln)
        return created

    def _ensure_domains(self):
        existing = {d.name for d in arcpy.da.ListDomains(self.gdb)}
        for name, (desc, codes) in DOMAINS.items():
            if name in existing:
                continue
            arcpy.management.CreateDomain(self.gdb, name, desc, "TEXT", "CODED")
            for code, label in codes:
                arcpy.management.AddCodedValueToDomain(self.gdb, name, code, label)

    def _create_fc(self, kind):
        path = self.fc[kind]
        arcpy.management.CreateFeatureclass(
            self.gdb, os.path.basename(path), "POLYGON" if kind == "area" else "POLYLINE",
            spatial_reference=sr_wgs84())
        for name, ftype, ln, alias, _dom in GDB_FIELDS:
            arcpy.management.AddField(path, name, ftype, field_length=ln, field_alias=alias)
        # Domains + defaults BEFORE subtypes so every subtype inherits them.
        for name, _t, _l, _a, dom in GDB_FIELDS:
            if dom:
                arcpy.management.AssignDomainToField(path, name, dom)
        for name, val in FIELD_DEFAULTS.items():
            arcpy.management.AssignDefaultToField(path, name, val)
        if kind == "line":
            arcpy.management.AssignDefaultToField(path, "line_width_m", DEFAULT_LINE_WIDTH_M)
        arcpy.management.SetSubtypeField(path, "tactic_code")
        for key, spec in TACTIC_TYPES.items():
            arcpy.management.AddSubtype(path, spec["code"], spec["label"])
        default = "dozer_line" if kind == "line" else "fuel_break"
        arcpy.management.SetDefaultSubtype(path, TACTIC_TYPES[default]["code"])

    # -- rows -------------------------------------------------------------- #
    @staticmethod
    def _row_props(kind, rec):
        """gdb record dict -> normalized WIRE props (all strings)."""
        ttype = CODE_TO_TYPE.get(rec.get("tactic_code")) or rec.get("tactic_type") or ""
        spec = TACTIC_TYPES.get(ttype, {})
        props = {}
        for name in WIRE_FIELDS:
            v = rec.get(name)
            if name in _DATE_FIELDS:
                props[name] = fmt_utc(parse_utc(v))
            elif name == "line_width_m":
                props[name] = fmt_width(v) if kind == "line" else ""
            else:
                props[name] = "" if v is None else str(v)
        props["tactic_type"] = ttype
        props["model_role"] = spec.get("model_role", props["model_role"])
        props["label"] = props["label"] or spec.get("label", "")
        props["visibility"] = norm_visibility(props["visibility"])
        props["simulate"] = norm_simulate(props["simulate"] or "true")
        props["status"] = props["status"] if props["status"] in (STATUS_ACTIVE, STATUS_INACTIVE) else STATUS_ACTIVE
        return props

    def read_rows(self, warn=None):
        """{tactic_id or '#kind:oid': row}. row = kind, oid, props, geom, hash, raw."""
        rows = {}
        for kind, path in self.fc.items():
            with arcpy.da.SearchCursor(path, CURSOR_FIELDS) as cur:
                for r in cur:
                    rec = dict(zip(CURSOR_FIELDS, r))
                    gj = arc_to_geojson(rec["SHAPE@"])
                    if gj is None or geom_family(gj) != kind:
                        if warn:
                            warn("Skipping %s feature OID %s: empty or unreadable shape."
                                 % (LAYER_NAMES[kind], rec["OID@"]))
                        continue
                    props = self._row_props(kind, rec)
                    tid = props["tactic_id"] or "#%s:%s" % (kind, rec["OID@"])
                    rows[tid] = dict(kind=kind, oid=rec["OID@"], props=props, geom=gj,
                                     raw=rec, hash=content_hash(props, gj))
        return rows

    @staticmethod
    def _to_record(kind, props):
        rec = {}
        for name, _t, _l, _a, _d in GDB_FIELDS:
            if name == "tactic_code":
                rec[name] = TACTIC_TYPES[props["tactic_type"]]["code"]
            elif name in _DATE_FIELDS:
                rec[name] = parse_utc(props.get(name))
            elif name == "line_width_m":
                w = fmt_width(props.get(name))
                rec[name] = float(w) if (w and kind == "line") else (
                    DEFAULT_LINE_WIDTH_M if kind == "line" else None)
            else:
                v = props.get(name, "")
                v = "" if v is None else str(v)
                rec[name] = v[: TEXT_LEN.get(name, 255)]
        return rec

    def update_fields(self, kind, oid, values):
        names = list(values)
        oid_field = arcpy.Describe(self.fc[kind]).OIDFieldName
        with arcpy.da.UpdateCursor(self.fc[kind], names, "%s = %d" % (oid_field, oid)) as cur:
            for row in cur:
                cur.updateRow([values[n] for n in names])

    def insert(self, kind, props, gj, epsg=None):
        rec = self._to_record(kind, props)
        names = list(rec) + ["SHAPE@"]
        geom = _with_sr(gj, epsg) if epsg else arcpy.AsShape(gj, False)
        if epsg:
            geom = project_to_wgs84(geom)
        with arcpy.da.InsertCursor(self.fc[kind], names) as cur:
            return cur.insertRow([rec[n] for n in rec] + [geom])

    def replace(self, row, props, gj):
        """Overwrite a row with a newer upstream version (may change kind)."""
        kind = geom_family(gj)
        if kind != row["kind"]:
            self.delete(row["kind"], row["oid"])
            self.insert(kind, props, gj)
            return
        rec = self._to_record(kind, props)
        names = list(rec) + ["SHAPE@"]
        oid_field = arcpy.Describe(self.fc[kind]).OIDFieldName
        with arcpy.da.UpdateCursor(self.fc[kind], names, "%s = %d" % (oid_field, row["oid"])) as cur:
            for _ in cur:
                cur.updateRow([rec[n] for n in rec] + [arcpy.AsShape(gj, False)])

    def delete(self, kind, oid):
        oid_field = arcpy.Describe(self.fc[kind]).OIDFieldName
        with arcpy.da.UpdateCursor(self.fc[kind], ["OID@"], "%s = %d" % (oid_field, oid)) as cur:
            for _ in cur:
                cur.deleteRow()

    # -- sync state -------------------------------------------------------- #
    def load_state(self):
        st = {}
        with arcpy.da.SearchCursor(self.state_table, ["tactic_id", "kind", "version", "version_id", "hash"]) as cur:
            for tid, kind, ver, vid, h in cur:
                st[tid] = {"kind": kind, "version": int(ver or 0), "version_id": vid or "", "hash": h or ""}
        return st

    def put_state(self, tid, entry):
        fields = ["tactic_id", "kind", "version", "version_id", "hash"]
        vals = [tid, entry.get("kind", ""), int(entry["version"]), entry.get("version_id", ""), entry.get("hash", "")]
        where = "tactic_id = '%s'" % tid.replace("'", "''")
        with arcpy.da.UpdateCursor(self.state_table, fields, where) as cur:
            for _ in cur:
                cur.updateRow(vals)
                return
        with arcpy.da.InsertCursor(self.state_table, fields) as cur:
            cur.insertRow(vals)

    def drop_state(self, tid):
        where = "tactic_id = '%s'" % tid.replace("'", "''")
        with arcpy.da.UpdateCursor(self.state_table, ["tactic_id"], where) as cur:
            for _ in cur:
                cur.deleteRow()


# =========================================================================== #
# Sync engine
# =========================================================================== #
MASS_DELETE_MIN = 3


def plan_version(state, status, chash):
    """Same rule as the QGIS plugin: (version_hint, op) or None."""
    if status == STATUS_INACTIVE:
        return None if state is None else (state["version"] + 1, "delete")
    if state is None:
        return 1, "create"
    if chash != state["hash"]:
        return state["version"] + 1, "edit"
    return None


class SyncEngine:
    """List -> push -> pull, all in one pass.

    Fixes vs the QGIS 0.3.0 sync:
      * Remote is listed BEFORE pushing and the next version is
        max(local, remote)+1, so editing a lineage someone else bumped no longer
        writes a duplicate version number (forked lineage / silently swallowed
        409).
      * Sync state is committed only after an upload succeeds, per tactic. A
        network failure mid-sync no longer marks unsent edits as synced.
      * Native deletes work: a synced tactic missing from the gdb is tombstoned
        (guarded against mass deletion).
    """

    def __init__(self, store, backend, session, log=print, warn=print,
                 allow_mass_delete=False, prefix=DISTURBANCE_PREFIX):
        self.store = store
        self.backend = backend
        self.author = session.get("author", "")
        self.org = session.get("org_id", "")
        self.log = log
        self.warn = warn
        self.allow_mass_delete = allow_mass_delete
        self.prefix = prefix.strip("/") + "/" if prefix else ""
        self.stats = dict(created=0, edited=0, deleted=0, added=0, updated=0,
                          removed=0, kept_local=0, retry=0)

    # -- helpers ----------------------------------------------------------- #
    def _key(self, lineage, version, vid):
        return "%s%s/%06d-%s.geojson" % (self.prefix, lineage, version, vid)

    def _payload(self, props, gj):
        p = {"paladin_schema": TACTIC_SCHEMA, "crs": WGS84}
        for name in WIRE_FIELDS:
            v = props.get(name, "")
            p[name] = "" if v is None else str(v)
        return {"type": "FeatureCollection",
                "features": [{"type": "Feature", "id": p["tactic_id"], "geometry": gj, "properties": p}]}

    def _push(self, lineage, props, gj, op, hint, st, remote):
        rem = remote.get(lineage)
        version = max(hint, (rem["version"] + 1) if rem else 0)
        if rem and (st is None or rem["version"] > st["version"]):
            supersedes = rem["version_id"]
        else:
            supersedes = st["version_id"] if st else ""
        vid = str(uuid.uuid4())
        out = dict(props)
        out.update(tactic_id=lineage, version=str(version), version_id=vid,
                   supersedes=supersedes, created_utc=utc_now_iso(),
                   author=self.author or props.get("author", ""),
                   org_id=props.get("org_id") or self.org,
                   status=STATUS_INACTIVE if op == "delete" else STATUS_ACTIVE)
        key = self._key(lineage, version, vid)
        if not self.backend.put_geojson(key, self._payload(out, gj)):
            self.stats["retry"] += 1
            self.warn("Version conflict on '%s' (someone saved at the same moment). "
                      "Left unsynced; run Sync again." % props.get("label", lineage))
            return None
        remote[lineage] = {"version": version, "version_id": vid, "key": key}
        return out, version, vid

    # -- main -------------------------------------------------------------- #
    def run(self, created_kinds=()):
        store = self.store
        self.log("Reading Paladin...")
        remote = index_remote(self.prefix, self.backend.list_keys(self.prefix))
        state = store.load_state()
        rows = store.read_rows(self.warn)

        # 1) Stamp identity on brand-new features drawn natively / imported.
        fixups = 0
        for tid, row in list(rows.items()):
            p, raw = row["props"], row["raw"]
            if not p["tactic_type"]:
                self.warn("Skipping %s OID %s: no tactic type." % (LAYER_NAMES[row["kind"]], row["oid"]))
                rows.pop(tid)
                continue
            vals = {}
            if not raw.get("tactic_id"):
                p["tactic_id"] = str(uuid.uuid4())
                vals.update(tactic_id=p["tactic_id"], source=raw.get("source") or "drawn",
                            created_utc=utc_now_iso(), author=self.author, org_id=self.org)
            for name in ("tactic_type", "model_role", "label", "visibility", "simulate", "status"):
                if (raw.get(name) or "") != p[name]:
                    vals[name] = p[name]
            if row["kind"] == "line" and not p["line_width_m"]:
                p["line_width_m"] = str(DEFAULT_LINE_WIDTH_M)
                vals["line_width_m"] = DEFAULT_LINE_WIDTH_M
            if vals:
                store.update_fields(row["kind"], row["oid"], vals)
                fixups += 1
        if fixups:
            rows = {k: v for k, v in store.read_rows().items() if v["props"]["tactic_type"]}

        # 2) Local deletes: synced lineage no longer in the gdb.
        missing = [t for t, s in state.items()
                   if t not in rows and s.get("kind") not in created_kinds]
        for t, s in state.items():
            if t not in rows and s.get("kind") in created_kinds:
                store.drop_state(t)   # layer was recreated - forget, don't tombstone
        if (len(missing) >= MASS_DELETE_MIN and len(missing) * 2 > len(state)
                and not self.allow_mass_delete):
            raise PaladinError(
                "%d of your %d synced tactics are missing from the Paladin layers. "
                "If you really deleted them, re-run Sync with 'Publish large "
                "deletions' checked (under Advanced). Otherwise nothing was "
                "changed." % (len(missing), len(state)))

        pushed = set()
        for t in missing:
            st = state[t]
            rem = remote.get(t)
            if rem is None:
                store.drop_state(t)
                continue
            last = self.backend.get_geojson(rem["key"])
            feat = first_feature(last)
            if not feat or (feat.get("properties") or {}).get("status") == STATUS_INACTIVE:
                store.drop_state(t)
                continue
            res = self._push(t, feat["properties"], feat["geometry"], "delete", st["version"] + 1, st, remote)
            if res:
                store.drop_state(t)
                self.stats["deleted"] += 1
                pushed.add(t)

        # 3) Push creates / edits / soft-deletes (status = inactive).
        for tid, row in rows.items():
            st = state.get(tid)
            if st is None and tid in remote:
                st_eff = None
                plan = (remote[tid]["version"] + 1, "edit")  # lost state: local wins
                if row["props"]["status"] == STATUS_INACTIVE:
                    plan = (remote[tid]["version"] + 1, "delete")
            else:
                st_eff = st
                plan = plan_version(st, row["props"]["status"], row["hash"])
            if plan is None:
                if row["props"]["status"] == STATUS_INACTIVE and st is None:
                    store.delete(row["kind"], row["oid"])     # never synced
                continue
            hint, op = plan
            res = self._push(tid, row["props"], row["geom"], op, hint, st_eff, remote)
            if res is None:
                continue
            out, version, vid = res
            pushed.add(tid)
            if op == "delete":
                store.delete(row["kind"], row["oid"])
                store.drop_state(tid)
            else:
                store.update_fields(row["kind"], row["oid"], {
                    "version": out["version"], "version_id": vid,
                    "supersedes": out["supersedes"], "created_utc": out["created_utc"],
                    "author": out["author"], "org_id": out["org_id"]})
                store.put_state(tid, {"kind": row["kind"], "version": version,
                                      "version_id": vid, "hash": row["hash"]})
            self.stats[{"create": "created", "edit": "edited", "delete": "deleted"}[op]] += 1
            self.log("Uploaded %s v%d (%s)" % (row["props"]["label"], version, op))

        # 4) Pull everything newer that we didn't just write.
        state = store.load_state()
        touched = set()
        for lineage, rem in remote.items():
            if lineage in pushed:
                continue
            st = state.get(lineage)
            if st and st["version"] >= rem["version"]:
                continue
            obj = self.backend.get_geojson(rem["key"])
            feat = first_feature(obj)
            if not feat:
                continue
            props = feat.get("properties") or {}
            if not visible_to(props, self.author, self.org):
                continue
            row = rows.get(lineage)
            local_dirty = row is not None and (st is None or row["hash"] != st["hash"])
            if props.get("status") == STATUS_INACTIVE:
                if row is not None and not local_dirty:
                    store.delete(row["kind"], row["oid"])
                    self.stats["removed"] += 1
                if row is None or not local_dirty:
                    store.drop_state(lineage)
                continue
            gj = feat.get("geometry")
            kind = geom_family(gj)
            ttype = props.get("tactic_type")
            if kind is None or ttype not in TACTIC_TYPES:
                continue
            if local_dirty:
                self.stats["kept_local"] += 1
                continue
            gj = to_multi(strip_z(gj))
            props = dict(props)
            props["tactic_id"] = lineage
            if row is None:
                store.insert(kind, props, gj)
                self.stats["added"] += 1
            else:
                store.replace(row, props, gj)
                self.stats["updated"] += 1
            touched.add((lineage, rem["version"], rem["version_id"]))

        # 5) Record hashes as the gdb actually stores them (post round-trip).
        if touched:
            fresh = store.read_rows()
            for lineage, ver, vid in touched:
                r = fresh.get(lineage)
                if r:
                    store.put_state(lineage, {"kind": r["kind"], "version": ver,
                                              "version_id": vid, "hash": r["hash"]})
        return self.stats


# =========================================================================== #
# Import
# =========================================================================== #
def import_into_store(store, source, tactic_type, visibility, simulate, width_m,
                      session, warn=print):
    """Import every line/polygon feature of `source` (layer, feature class, or
    .geojson/.json/.kml/.kmz path). Geometry kind follows the DATA, never the
    tactic type (the QGIS build forced e.g. polygons imported as 'Dozer Line'
    into the line layer). Points are skipped."""
    items, epsg, fname = [], None, ""
    low = str(source).lower()
    if low.endswith((".geojson", ".json")):
        items, epsg = read_geojson_file(source)
        fname = os.path.basename(source)
    elif low.endswith((".kml", ".kmz")):
        items, epsg = read_kml_file(source)
        fname = os.path.basename(source)
    else:
        try:
            fname = arcpy.Describe(source).name
        except Exception:  # noqa: BLE001
            fname = os.path.basename(str(source))
        with arcpy.da.SearchCursor(source, ["SHAPE@"]) as cur:
            for (geom,) in cur:
                items.append((arc_to_geojson(geom), ""))
    added, skipped = 0, 0
    spec = TACTIC_TYPES[tactic_type]
    for gj, name in items:
        kind = geom_family(gj)
        if kind is None:
            skipped += 1
            continue
        gj = to_multi(strip_z(gj))
        props = {n: "" for n in WIRE_FIELDS}
        props.update(
            tactic_id=str(uuid.uuid4()), status=STATUS_ACTIVE, tactic_type=tactic_type,
            model_role=spec["model_role"], label=(name or spec["label"]),
            created_utc=utc_now_iso(), org_id=session.get("org_id", ""),
            author=session.get("author", ""), visibility=norm_visibility(visibility),
            simulate="true" if simulate else "false", source="imported",
            source_file=fname, line_width_m=str(width_m) if kind == "line" else "")
        store.insert(kind, props, gj, epsg=epsg)
        added += 1
    if skipped:
        warn("Skipped %d point/unsupported feature(s); tactics must be lines or polygons." % skipped)
    return added


# =========================================================================== #
# Map helpers (ArcGIS Pro)
# =========================================================================== #
def current_project():
    try:
        return arcpy.mp.ArcGISProject("CURRENT")
    except Exception:  # noqa: BLE001 - not running inside Pro
        return None


def gdb_path():
    override = os.environ.get("PALADIN_GDB")
    if override:
        return override
    aprx = current_project()
    if aprx is None:
        raise PaladinError("Open an ArcGIS Pro project first.")
    return os.path.join(aprx.homeFolder, "Paladin.gdb")


def target_map(aprx):
    m = aprx.activeMap
    if m is None:
        maps = aprx.listMaps()
        m = maps[0] if maps else aprx.createMap("Paladin")
    return m


def _norm(p):
    return os.path.normcase(os.path.normpath(str(p)))


def ensure_layers_in_map(store, log=print):
    aprx = current_project()
    if aprx is None:
        return
    m = target_map(aprx)
    have = set()
    for lyr in m.listLayers():
        try:
            if lyr.supports("DATASOURCE"):
                have.add(_norm(lyr.dataSource))
        except Exception:  # noqa: BLE001
            continue
    for kind in ("area", "line"):
        if _norm(store.fc[kind]) in have:
            continue
        lyr = m.addDataFromPath(store.fc[kind])
        try:
            lyr.name = LAYER_NAMES[kind]
        except Exception:  # noqa: BLE001
            pass
        style_layer(lyr, kind)
        log("Added '%s' to map '%s'." % (LAYER_NAMES[kind], m.name))


def _color_symbol(symbol, ttype, kind):
    r, g, b, a = TACTIC_TYPES[ttype]["color"]
    if kind == "line":
        symbol.color = {"RGB": [r, g, b, 100]}
        symbol.width = 2.5
    else:
        symbol.color = {"RGB": [r, g, b, a]}
        symbol.outlineColor = {"RGB": [r, g, b, 100]}
        symbol.outlineWidth = 1.5


def style_layer(lyr, kind):
    """Best effort: color each tactic class. Never fails the tool."""
    targets = [lyr]
    try:
        if getattr(lyr, "isGroupLayer", False):   # subtype group layer
            targets = lyr.listLayers()
    except Exception:  # noqa: BLE001
        pass
    for t in targets:
        try:
            sym = t.symbology
            ren = sym.renderer
            if ren.type == "UniqueValueRenderer":
                for grp in ren.groups:
                    for itm in grp.items:
                        ttype = LABEL_TO_TYPE.get(itm.label)
                        if ttype:
                            _color_symbol(itm.symbol, ttype, kind)
            elif ren.type == "SimpleRenderer" and LABEL_TO_TYPE.get(t.name):
                _color_symbol(ren.symbol, LABEL_TO_TYPE[t.name], kind)
            t.symbology = sym
        except Exception:  # noqa: BLE001
            continue


def _edits_hint(exc):
    msg = str(exc)
    if any(s in msg.lower() for s in ("lock", "edit session", "transaction", "being edited")):
        return PaladinError("The Paladin layers have unsaved edits. Save them "
                            "(Edit tab -> Save) and run the tool again.")
    return None


def open_store():
    store = GdbStore(gdb_path())
    created = store.ensure()
    return store, created


# =========================================================================== #
# Toolbox
# =========================================================================== #
def _param(display, name, datatype, required=True, category=None, multi=False):
    p = arcpy.Parameter(displayName=display, name=name, datatype=datatype,
                        parameterType="Required" if required else "Optional",
                        direction="Input", multiValue=multi, category=category)
    return p


def _run(messages, fn):
    """Uniform error surface: friendly message, no stack trace for users."""
    try:
        fn()
    except PaladinError as exc:
        messages.addErrorMessage(str(exc))
        raise arcpy.ExecuteError(str(exc))
    except RuntimeError as exc:
        friendly = _edits_hint(exc)
        if friendly:
            messages.addErrorMessage(str(friendly))
            raise arcpy.ExecuteError(str(friendly))
        raise


class Toolbox:
    def __init__(self):
        self.label = "Paladin"
        self.alias = "paladin"
        self.description = "Paladin wildfire tactics: sign in, draw, import, sync. v" + TOOLBOX_VERSION
        self.tools = [SignIn, AddTacticLayers, SyncWithPaladin, ImportTactics,
                      AddLandfire, SignOut]


class SignIn:
    def __init__(self):
        self.label = "Sign In"
        self.description = ("Sign in with your Paladin account. Your password is "
                            "never stored; you stay signed in on this computer.")

    def getParameterInfo(self):
        email = _param("Email", "email", "GPString")
        email.value = load_session().get("email", "") or None
        pw = _param("Password", "password", "GPStringHidden")
        ep = _param("Login endpoint", "endpoint", "GPString", required=False, category="Advanced")
        ep.value = AUTH_ENDPOINT
        return [email, pw, ep]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        return

    def execute(self, parameters, messages):
        def go():
            email = (parameters[0].valueAsText or "").strip()
            password = parameters[1].valueAsText or ""
            endpoint = (parameters[2].valueAsText or AUTH_ENDPOINT).strip()
            res = auth_call(endpoint, "/login", {"email": email, "password": password})
            if not res.get("refresh_token"):
                raise PaladinError("Login did not return a session. Contact support@paladinindustries.com.")
            session = {"email": res.get("email", email),
                       "endpoint": endpoint if endpoint.rstrip("/") != AUTH_ENDPOINT.rstrip("/") else "",
                       "author": res.get("author", ""), "org_id": res.get("org_id", ""),
                       "tier": res.get("tier", ""), "token": res.get("token", ""),
                       "refresh_token": res.get("refresh_token", "")}
            save_session(session)
            messages.addMessage("Signed in as %s (tier: %s)." % (session["email"], session["tier"] or "?"))
            if current_project() is not None:
                store, _ = open_store()
                ensure_layers_in_map(store, messages.addMessage)
                messages.addMessage("Draw tactics from Edit -> Create (pick a template under "
                                    "'Paladin Tactics'), then run 'Sync with Paladin'.")
        _run(messages, go)


class SignOut:
    def __init__(self):
        self.label = "Sign Out"
        self.description = "Forget the Paladin session on this computer."

    def getParameterInfo(self):
        return []

    def execute(self, parameters, messages):
        s = load_session()
        save_session({"email": s.get("email", "")})
        messages.addMessage("Signed out.")


class AddTacticLayers:
    def __init__(self):
        self.label = "Add Tactic Layers"
        self.description = ("Adds the editable Paladin tactic layers (Areas + Lines) "
                            "to the current map. Draw with Edit -> Create.")

    def getParameterInfo(self):
        return []

    def execute(self, parameters, messages):
        def go():
            store, created = open_store()
            if created:
                messages.addMessage("Created %s" % store.gdb)
            ensure_layers_in_map(store, messages.addMessage)
        _run(messages, go)


class SyncWithPaladin:
    def __init__(self):
        self.label = "Sync with Paladin"
        self.description = ("Uploads your new, edited and deleted tactics, then "
                            "downloads everything shared with you. Save your "
                            "edits first (Edit tab -> Save).")

    def getParameterInfo(self):
        p = _param("Publish large deletions", "allow_mass_delete", "GPBoolean",
                   required=False, category="Advanced")
        p.value = False
        return [p]

    def updateMessages(self, parameters):
        if not signed_in(load_session()):
            parameters[0].setWarningMessage("Not signed in - run 'Sign In' first.")

    def execute(self, parameters, messages):
        def go():
            session = load_session()
            if not signed_in(session):
                raise PaladinError("Not signed in. Run the 'Sign In' tool first.")
            store, created = open_store()
            engine = SyncEngine(store, McleodBackend(session), session,
                                log=messages.addMessage, warn=messages.addWarningMessage,
                                allow_mass_delete=bool(parameters[0].value))
            s = engine.run(created_kinds=created)
            ensure_layers_in_map(store, messages.addMessage)
            messages.addMessage(
                "Sync complete. Uploaded: %d new, %d edited, %d deleted. "
                "Downloaded: %d new, %d updated, %d removed."
                % (s["created"], s["edited"], s["deleted"], s["added"], s["updated"], s["removed"]))
            if s["retry"]:
                messages.addWarningMessage("%d tactic(s) need another Sync." % s["retry"])
        _run(messages, go)


class ImportTactics:
    def __init__(self):
        self.label = "Import Tactics"
        self.description = ("Copy lines/polygons from a map layer (respects selection), "
                            "a feature class, or a GeoJSON/KML/KMZ file into Paladin.")

    def getParameterInfo(self):
        src = _param("Layer or file (GeoJSON / KML / KMZ)", "source", ["GPFeatureLayer", "DEFile"])
        ttype = _param("These features are", "tactic_type", "GPString")
        ttype.filter.type = "ValueList"
        ttype.filter.list = [s["label"] for s in TACTIC_TYPES.values()]
        vis = _param("Who can see them", "visibility", "GPString")
        vis.filter.type = "ValueList"
        vis.filter.list = VISIBILITY_LEVELS
        vis.value = DEFAULT_VISIBILITY
        sim = _param("Include in simulations", "simulate", "GPBoolean", required=False)
        sim.value = True
        width = _param("Line width (m) - lines only", "width", "GPDouble", required=False)
        width.value = DEFAULT_LINE_WIDTH_M
        return [src, ttype, vis, sim, width]

    def updateMessages(self, parameters):
        src = parameters[0]
        if src.altered and src.valueAsText:
            t = src.valueAsText.lower()
            if os.path.isfile(src.valueAsText) and not t.endswith((".geojson", ".json", ".kml", ".kmz")):
                src.setErrorMessage("Files must be .geojson, .json, .kml or .kmz (or pick a map layer).")
        w = parameters[4]
        if w.value is not None and w.value <= 0:
            w.setErrorMessage("Width must be greater than 0.")

    def execute(self, parameters, messages):
        def go():
            store, _ = open_store()
            n = import_into_store(
                store, parameters[0].valueAsText, LABEL_TO_TYPE[parameters[1].valueAsText],
                parameters[2].valueAsText, bool(parameters[3].value),
                float(parameters[4].value or DEFAULT_LINE_WIDTH_M), load_session(),
                warn=messages.addWarningMessage)
            ensure_layers_in_map(store, messages.addMessage)
            messages.addMessage("Imported %d tactic(s). Run 'Sync with Paladin' to publish." % n)
        _run(messages, go)


class AddLandfire:
    def __init__(self):
        self.label = "Add LANDFIRE Layers"
        self.description = "Add LANDFIRE LF2025 CONUS fuels image services to the current map."

    def getParameterInfo(self):
        p = _param("Layers", "layers", "GPString", multi=True)
        p.filter.type = "ValueList"
        p.filter.list = [label for label, _ in LANDFIRE_CATALOG]
        return [p]

    def execute(self, parameters, messages):
        def go():
            aprx = current_project()
            if aprx is None:
                raise PaladinError("Open an ArcGIS Pro project first.")
            m = target_map(aprx)
            products = dict(LANDFIRE_CATALOG)
            for label in parameters[0].values or []:
                try:
                    lyr = m.addDataFromPath(landfire_url(products[label]))
                    lyr.name = "LANDFIRE " + label.split(": ", 1)[-1]
                    messages.addMessage("Added %s" % lyr.name)
                except Exception as exc:  # noqa: BLE001
                    messages.addWarningMessage("Couldn't add %s (%s). The service may be renamed." % (label, exc))
        _run(messages, go)
