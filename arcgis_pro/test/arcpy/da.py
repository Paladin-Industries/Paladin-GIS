import re, copy
from . import management as M

class _Dom:
    def __init__(self, n): self.name = n
def ListDomains(ws): return [_Dom(n) for n in M.DB["domains"].get(ws, {})]

def _match(where):
    if not where: return lambda oid, r: True
    m = re.match(r"(\w+) = (\d+)$", where)
    if m: n = int(m.group(2)); return lambda oid, r: oid == n
    m = re.match(r"(\w+) = '(.*)'$", where)
    f, v = m.group(1), m.group(2).replace("''", "'")
    return lambda oid, r: r.get(f) == v

def _get(t, oid, r, f):
    if f == "OID@": return oid
    if f == "SHAPE@":
        from . import Geometry, SpatialReference
        return None if r.get("SHAPE") is None else Geometry(r["SHAPE"], SpatialReference(4326))
    return copy.deepcopy(r.get(f))

def _put(t, r, f, v):
    from . import _esri_reorient
    if f == "SHAPE@":
        gj = v.__geo_interface__
        r["SHAPE"] = _esri_reorient(gj); return
    ft, ln = t["fields"][f]
    if ft == "TEXT" and v is not None:
        assert isinstance(v, str), (f, v); v = v[:ln]
    if ft == "DOUBLE" and v is not None: v = float(v)
    if ft == "DATE" and v is not None: assert hasattr(v, "year"), (f, v)
    r[f] = v

class SearchCursor:
    def __init__(self, path, fields, where=None):
        self.t = M.DB["tables"][path]; self.f = fields; self.m = _match(where)
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def __iter__(self):
        for oid, r in list(self.t["rows"].items()):
            if self.m(oid, r): yield tuple(_get(self.t, oid, r, f) for f in self.f)

class UpdateCursor(SearchCursor):
    def __iter__(self):
        for oid, r in list(self.t["rows"].items()):
            if self.m(oid, r):
                self._oid = oid
                yield [_get(self.t, oid, r, f) for f in self.f]
    def updateRow(self, vals):
        r = self.t["rows"][self._oid]
        for f, v in zip(self.f, vals):
            if f != "OID@": _put(self.t, r, f, v)
    def deleteRow(self): del self.t["rows"][self._oid]

class InsertCursor:
    def __init__(self, path, fields): self.t = M.DB["tables"][path]; self.f = fields
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def insertRow(self, vals):
        r = {k: v for k, v in self.t["defaults"].items()}
        for f, v in zip(self.f, vals): _put(self.t, r, f, v)
        oid = self.t["next"]; self.t["next"] += 1; self.t["rows"][oid] = r; return oid
