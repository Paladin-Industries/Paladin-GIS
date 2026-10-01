"""Minimal in-memory arcpy stand-in for testing PaladinGIS.pyt logic."""
import copy, math, os, re, datetime
from . import da, management, mp  # noqa

class ExecuteError(Exception): pass

class SpatialReference:
    def __init__(self, code=4326):
        self.factoryCode = code
    def __eq__(self, o): return getattr(o, "factoryCode", None) == self.factoryCode

def _snap(c):
    if c and isinstance(c[0], (int, float)):
        return [round(round(c[0] / 1e-9) * 1e-9, 9), round(round(c[1] / 1e-9) * 1e-9, 9)]
    return [_snap(x) for x in c]

class Geometry:
    def __init__(self, gj, sr):
        self._gj = copy.deepcopy(gj); self.spatialReference = sr
        self.hasCurves = False; self.isMultipart = True
    @property
    def __geo_interface__(self):
        return copy.deepcopy(self._gj)
    def projectAs(self, sr, xform=""):
        src = self.spatialReference.factoryCode
        def walk(c):
            if c and isinstance(c[0], (int, float)):
                x, y = c[0], c[1]
                if src == 3857:
                    lon = x / 6378137.0 * 180 / math.pi
                    lat = (2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2) * 180 / math.pi
                    return [lon, lat]
                return [x, y]
            return [walk(i) for i in c]
        return Geometry({"type": self._gj["type"], "coordinates": walk(self._gj["coordinates"])}, sr)

def _esri_reorient(gj):
    """Simulate a gdb write: snap to 1e-9 grid, reverse polygon rings (Esri CW)."""
    t, c = gj["type"], _snap(gj["coordinates"])
    if t == "Polygon": c = [list(reversed(r)) for r in c]
    if t == "MultiPolygon": c = [[list(reversed(r)) for r in p] for p in c]
    return {"type": t, "coordinates": c}

def AsShape(obj, esri_json=False):
    if esri_json:
        sr = SpatialReference(obj["spatialReference"]["wkid"])
        if "paths" in obj: return Geometry({"type": "MultiLineString", "coordinates": obj["paths"]}, sr)
        return Geometry({"type": "MultiPolygon", "coordinates": [[r] for r in obj["rings"]]}, sr)
    return Geometry(obj, SpatialReference(0))

def ListTransformations(a, b): return []

def Exists(path): return management._exists(path)

class _Desc:
    def __init__(self, path): self.OIDFieldName = "OBJECTID"; self.name = os.path.basename(path)
def Describe(path): return _Desc(path)

class Parameter:
    def __init__(self, **kw):
        self.__dict__.update(kw); self.value = None; self.filter = type("F", (), {})()
