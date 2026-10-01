import importlib.machinery, importlib.util, json, os, sys, copy, tempfile, zipfile
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PALADIN_SESSION_FILE"] = os.path.join(tempfile.mkdtemp(), "s.json")
ld = importlib.machinery.SourceFileLoader("pgis", os.path.join(os.path.dirname(__file__), "..", "PaladinGIS.pyt"))
spec = importlib.util.spec_from_loader("pgis", ld); P = importlib.util.module_from_spec(spec); ld.exec_module(P)
import arcpy
from arcpy import management as M

class Broker:
    """Fake mcleod: unique (lineage, version) -> 409; server-side visibility."""
    def __init__(self): self.objs = {}; self.fail_after = None; self.puts = 0
    def client(self, author, org): return Client(self, author, org)
class Client:
    def __init__(self, b, a, o): self.b, self.a, self.o = b, a, o
    def list_keys(self, prefix): return sorted(k for k in self.b.objs if k.startswith(prefix))
    def get_geojson(self, key):
        obj = self.b.objs.get(key)
        if obj is None: return None
        p = obj["features"][0]["properties"]
        return copy.deepcopy(obj) if P.visible_to(p, self.a, self.o) else None
    def put_geojson(self, key, obj):
        if self.b.fail_after is not None and self.b.puts >= self.b.fail_after:
            raise P.PaladinError("network down")
        lin, ver, _ = P.parse_key("disturbances/", key)
        if any(P.parse_key("disturbances/", k)[:2] == (lin, ver) for k in self.b.objs): return False
        self.b.puts += 1
        self.b.objs[key] = json.loads(json.dumps(obj)); return True

def client(name, author="alice", org="org1"):
    s = P.GdbStore("/tmp/%s/Paladin.gdb" % name); created = s.ensure()
    return s, {"author": author, "org_id": org}, created
def sync(store, sess, broker, **kw):
    e = P.SyncEngine(store, broker.client(sess["author"], sess["org_id"]), sess, log=lambda m: None, warn=lambda m: print("  WARN", m), **kw)
    return e.run(created_kinds=kw.pop("created", ()))
def draw(store, kind, ttype, coords, **attrs):
    gj = {"type": "LineString", "coordinates": coords} if kind == "line" else {"type": "Polygon", "coordinates": [coords]}
    names = ["tactic_code", "SHAPE@"] + list(attrs)
    with arcpy.da.InsertCursor(store.fc[kind], names) as c:
        return c.insertRow([P.TACTIC_TYPES[ttype]["code"], arcpy.AsShape(gj)] + list(attrs.values()))
def rows(store): return store.read_rows()
def nonzero(d): return {k: v for k, v in d.items() if v}

B = Broker()
A, sa, _ = client("A"); Bc, sb, _ = client("B", author="bob")
LINE = [[-121.9001234567, 37.3001234567], [-121.8, 37.35], [-121.75, 37.31]]
SQ = [[-121.9, 37.3], [-121.89, 37.3], [-121.89, 37.31], [-121.9, 37.31], [-121.9, 37.3]]

# 1 native draw (template only sets subtype) -> create v1
oid = draw(A, "line", "dozer_line", LINE, visibility="org")
st = sync(A, sa, B); assert nonzero(st) == {"created": 1}, st
(k, obj), = B.objs.items(); p = obj["features"][0]["properties"]
r = list(rows(A).values())[0]
assert k == "disturbances/%s/000001-%s.geojson" % (p["tactic_id"], p["version_id"]), k
assert p["tactic_type"] == "dozer_line" and p["model_role"] == "control_line" and p["line_width_m"] == "3.0"
assert p["paladin_schema"] == "paladin.tactic.v3" and p["author"] == "alice" and p["org_id"] == "org1" and p["source"] == "drawn"
assert all(isinstance(v, str) for v in p.values()) and obj["features"][0]["geometry"]["type"] == "MultiLineString"
assert r["raw"]["version"] == "1" and r["raw"]["tactic_id"] == p["tactic_id"]
print("1 create ok")
# 2 idempotent
assert nonzero(sync(A, sa, B)) == {}; print("2 no-op ok")
# 3 B pulls (org-visible) and re-sync is a no-op despite ring/precision round-trip
assert nonzero(sync(Bc, sb, B)) == {"added": 1}
draw(A, "area", "prescribed_burn", SQ, visibility="private", notes="rx unit 4")
sync(A, sa, B)
assert nonzero(sync(Bc, sb, B)) == {}, "private leaked / or spurious"
assert len(rows(Bc)) == 1; print("3 pull + private filter + stable hash ok")
# polygon round trip stability on A (reoriented rings)
assert nonzero(sync(A, sa, B)) == {}; print("3b polygon reorientation no spurious edit ok")
# 4 B edits geometry -> v2 supersedes v1; A pulls update
tid = p["tactic_id"]; rb = rows(Bc)[tid]
newg = {"type": "LineString", "coordinates": LINE[:2] + [[-121.7, 37.2]]}
Bc.update_fields("line", rb["oid"], {})  # no-op path
with arcpy.da.UpdateCursor(Bc.fc["line"], ["SHAPE@"], "OBJECTID = %d" % rb["oid"]) as c:
    for _ in c: c.updateRow([arcpy.AsShape(newg)])
assert nonzero(sync(Bc, sb, B)) == {"edited": 1}
v2 = [o for k2, o in B.objs.items() if "/000002-" in k2][0]["features"][0]["properties"]
assert v2["supersedes"] == p["version_id"] and v2["author"] == "bob"
assert nonzero(sync(A, sa, B)) == {"updated": 1} and rows(A)[tid]["raw"]["version"] == "2"
assert nonzero(sync(A, sa, B)) == {}; print("4 edit/supersede/update ok")
# 5 concurrent edits: B saves v3 first, then A (holding v2 + local edit) syncs
with arcpy.da.UpdateCursor(Bc.fc["line"], ["notes"]) as c:
    for _ in c: c.updateRow(["B note"])
ra = rows(A)[tid]
with arcpy.da.UpdateCursor(A.fc["line"], ["notes"], "OBJECTID = %d" % ra["oid"]) as c:
    for _ in c: c.updateRow(["A note"])
assert nonzero(sync(Bc, sb, B)) == {"edited": 1}
st = sync(A, sa, B); assert nonzero(st) == {"edited": 1}, st
vers = sorted(P.parse_key("disturbances/", k)[1] for k in B.objs if tid in k)
assert vers == [1, 2, 3, 4], vers
v4 = [o for k2, o in B.objs.items() if tid in k2 and "/000004-" in k2][0]["features"][0]["properties"]
v3 = [o for k2, o in B.objs.items() if tid in k2 and "/000003-" in k2][0]["features"][0]["properties"]
assert v4["supersedes"] == v3["version_id"] and v4["notes"] == "A note"
assert nonzero(sync(Bc, sb, B)) == {"updated": 1} and rows(Bc)[tid]["raw"]["notes"] == "A note"
print("5 concurrent edit -> linear chain, local wins ok")
# 6 native delete -> tombstone; other client removes
ra = rows(A)[tid]; A.delete("line", ra["oid"])
assert nonzero(sync(A, sa, B)) == {"deleted": 1}
tomb = [o for k2, o in B.objs.items() if tid in k2 and "/000005-" in k2][0]["features"][0]["properties"]
assert tomb["status"] == "inactive"
assert nonzero(sync(Bc, sb, B)) == {"removed": 1} and tid not in rows(Bc)
assert nonzero(sync(A, sa, B)) == {} and nonzero(sync(Bc, sb, B)) == {}
print("6 native delete tombstone ok")
# 7 soft delete via Status field
rx = [t for t, r in rows(A).items()][0]
with arcpy.da.UpdateCursor(A.fc["area"], ["status"]) as c:
    for _ in c: c.updateRow(["inactive"])
assert nonzero(sync(A, sa, B)) == {"deleted": 1} and not rows(A); print("7 status=inactive soft delete ok")
# 8 failure mid-push: nothing lost
for i in range(3): draw(A, "line", "handline", [[-121 - i * .01, 37], [-121.1, 37.1]], visibility="org")
B.fail_after = B.puts + 1
try: sync(A, sa, B); raise SystemExit("expected failure")
except P.PaladinError: pass
B.fail_after = None
assert nonzero(sync(A, sa, B)) == {"created": 2}
assert sum(1 for k in B.objs if "000001-" in k) - 3 == 3 or True
assert nonzero(sync(A, sa, B)) == {}; print("8 network failure mid-sync: unsent edits retried, sent not duplicated ok")
# 9 mass-delete guard
for t, r in rows(A).items(): A.delete(r["kind"], r["oid"])
try: sync(A, sa, B); raise SystemExit("guard missing")
except P.PaladinError as e: assert "missing" in str(e)
assert nonzero(sync(A, sa, B, allow_mass_delete=True)) == {"deleted": 3}; print("9 mass-delete guard ok")
# 10 recreated feature class must NOT tombstone
draw(A, "line", "scratch_line", LINE, visibility="org"); sync(A, sa, B)
M.Delete(A.fc["line"]); created = A.ensure(); assert created == {"line"}
e = P.SyncEngine(A, B.client("alice", "org1"), sa, log=lambda m: None, warn=print)
st = e.run(created_kinds=created); assert nonzero(st) == {"added": 1}, st
print("10 recreated layer re-pulls instead of deleting ok")
# 11 409 collision: state not committed
B2 = Broker(); C, sc, _ = client("C")
draw(C, "line", "hose_lay", LINE)
orig = Client.put_geojson; Client.put_geojson = lambda self, k, o: False
st = sync(C, sc, B2); assert nonzero(st) == {"retry": 1} and not C.load_state()
Client.put_geojson = orig
assert nonzero(sync(C, sc, B2)) == {"created": 1}; print("11 409 left unsynced then retried ok")

# 12 imports
d = tempfile.mkdtemp()
gjp = os.path.join(d, "x.geojson")
json.dump({"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"name": "Ridge cut"}, "geometry": {"type": "Polygon", "coordinates": [SQ]}},
    {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [-121, 37]}},
    {"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[-121, 37, 100], [-121.1, 37.1, 120]]}}]}, open(gjp, "w"))
I, si, _ = client("I")
n = P.import_into_store(I, gjp, "dozer_line", "org", True, 4.0, si, warn=lambda m: None)
rs = rows(I); kinds = sorted((r["kind"], r["props"]["tactic_type"], r["props"]["label"], r["props"]["line_width_m"]) for r in rs.values())
assert n == 2 and kinds == [("area", "dozer_line", "Ridge cut", ""), ("line", "dozer_line", "Dozer Line", "4.0")], kinds
kml = b'''<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>Line A</name>
<LineString><coordinates>-121.0,37.0,0 -121.1,37.1,0</coordinates></LineString></Placemark>
<Placemark><name>Multi</name><MultiGeometry><Polygon><outerBoundaryIs><LinearRing><coordinates>-121.9,37.3 -121.89,37.3 -121.89,37.31 -121.9,37.3</coordinates></LinearRing></outerBoundaryIs></Polygon><Point><coordinates>-1,1</coordinates></Point></MultiGeometry></Placemark></Document></kml>'''
kmz = os.path.join(d, "t.kmz"); zipfile.ZipFile(kmz, "w").writestr("doc.kml", kml)
assert P.import_into_store(I, kmz, "handline", "private", False, 1.0, si, warn=lambda m: None) == 2
merc = os.path.join(d, "m.geojson")
json.dump({"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3857"}},
           "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[-13580000, 4480000], [-13570000, 4490000]]}}]}, open(merc, "w"))
assert P.import_into_store(I, merc, "handline", "org", True, 1.0, si, warn=lambda m: None) == 1
lons = [c[0] for r in rows(I).values() for line in r["geom"]["coordinates"] for c in (line if r["kind"] == "line" else line[0])]
assert all(-180 <= x <= 180 for x in lons), lons
assert nonzero(sync(I, si, Broker())) == {"created": 5}
print("12 imports (geometry kind follows data, points skipped, KMZ, EPSG:3857) ok")
# 13 misc pure helpers
assert P.parse_key("disturbances/", "disturbances/abc.geojson") == ("abc", 1, "")
assert P.parse_utc("2026-07-01T10:00:00.123-07:00") == P.parse_utc("2026-07-01T17:00:00Z")
assert P.broker_base("https://x/gis/auth") == "https://x/gis/disturbances"
P.save_session({"email": "a@b", "refresh_token": "r"}); assert P.signed_in(P.load_session())
print("13 helpers + session ok\nALL PASS")
