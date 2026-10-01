import os, sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import stubs, copy, json, sys, urllib.error, io
from paladin_gis import panel, s3_client, sync_plan, paladin_auth, config
PFX = "disturbances/"
class Broker:
    def __init__(self): self.objs = {}; self.fail_after = None; self.puts = 0
    def list_keys(self, prefix): return sorted(k for k in self.objs if k.startswith(prefix))
    def get_geojson(self, key): return copy.deepcopy(self.objs.get(key))
    def put_geojson(self, key, obj):
        if self.fail_after is not None and self.puts >= self.fail_after: raise s3_client.SyncError("net down")
        lin, v, _ = sync_plan.parse_key(PFX, key)
        if any(sync_plan.parse_key(PFX, k)[:2] == (lin, v) for k in self.objs): return False
        self.puts += 1; self.objs[key] = json.loads(json.dumps(obj)); return True
B = Broker(); s3_client.make_backend = lambda s: B

def feat(lineage, label="x", vis="org"):
    p = {f: "" for f, _ in config.TACTIC_FIELDS}
    p.update(tactic_id=lineage, tactic_type="dozer_line", visibility=vis, label=label, org_id="org1")
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "id": lineage, "properties": p,
            "geometry": {"type": "MultiLineString", "coordinates": [[[0, 0], [1, 1]]]}}]}
def run(pending, have=None, author="alice"):
    ev = {"uploaded": [], "conflict": [], "downloaded": [], "done": None, "failed": None}
    w = panel.SyncWorker({}, pending, PFX, have=have or {}, my_org="org1", my_author=author)
    w.uploaded.connect(ev["uploaded"].append); w.conflict.connect(ev["conflict"].append)
    w.downloaded.connect(ev["downloaded"].append)
    w.finished_ok.connect(lambda *a: ev.__setitem__("done", a)); w.failed.connect(lambda m: ev.__setitem__("failed", m))
    w.start(); return ev
def item(lin, op, hint, state=None, h="h1"):
    return dict(lineage=lin, op=op, hint=hint, hash=h, label=lin, state=state, obj=feat(lin))

# 1 create
e = run([item("L1", "create", 1)]); u = e["uploaded"][0]
assert e["done"] == (1, 0, 0) and u["version"] == 1 and u["supersedes"] == ""
k = sync_plan.object_key(PFX, "L1", 1, u["version_id"]); p = B.objs[k]["features"][0]["properties"]
assert p["version"] == "1" and p["author"] == "alice" and p["status"] == "active"
st1 = {"version": 1, "version_id": u["version_id"], "hash": "h1"}
print("1 create ok")
# 2 bob (other client, same org) edits -> v2; alice with stale state edits -> must be v3 superseding bob's v2
e = run([item("L1", "edit", 2, state=st1, h="hb")], have={"L1": 1}, author="bob"); vb = e["uploaded"][0]
e = run([item("L1", "edit", 2, state=st1, h="ha")], have={"L1": 1})
u = e["uploaded"][0]; assert u["version"] == 3 and u["supersedes"] == vb["version_id"], u
assert sorted(sync_plan.parse_key(PFX, k)[1] for k in B.objs) == [1, 2, 3]
print("2 concurrent edit -> v3 supersedes v2, no fork ok")
# 3 failure mid-push: only confirmed uploads are reported (and so committed)
B.fail_after = B.puts + 1
e = run([item("N1", "create", 1), item("N2", "create", 1), item("N3", "create", 1)])
assert [x["lineage"] for x in e["uploaded"]] == ["N1"] and e["failed"] and e["done"] is None
B.fail_after = None; print("3 mid-sync failure commits only N1 ok")
# 4 409: race simulated by a broker that always reports collision
orig = B.put_geojson; B.put_geojson = lambda k, o: False
e = run([item("R1", "create", 1)]); assert e["uploaded"] == [] and e["conflict"] == ["R1"] and e["done"][0] == 0 and e["done"][2] == 1
B.put_geojson = orig; print("4 409 -> conflict, nothing committed ok")
# 5 pull: skips lineages we just pushed + versions we hold; legacy flat key not re-downloaded
B.objs["disturbances/LEG.geojson"] = feat("LEG", vis="public")
e = run([], have={"L1": 3, "N1": 1, "LEG": 1}); assert e["downloaded"] == [], [d["features"][0]["id"] for d in e["downloaded"]]
e = run([], have={}); ids = sorted(d["features"][0]["id"] for d in e["downloaded"])
assert ids == ["L1", "LEG", "N1"], ids
print("5 pull + legacy key ok")
# 6 delete -> tombstone status inactive
e = run([item("N1", "delete", 2, state={"version": 1, "version_id": "x", "hash": "h"})])
k = [k for k in B.objs if "N1/000002-" in k][0]; assert B.objs[k]["features"][0]["properties"]["status"] == "inactive"
print("6 tombstone ok")
# 7 auth: HTML 403 (Cloudflare) vs JSON 403 (license)
def fake(code, body):
    def f(req, timeout=0):
        assert req.headers.get("User-agent", "").startswith("PaladinGIS-QGIS/0.3.1"), req.headers
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(body))
    return f
paladin_auth.urllib.request.urlopen = fake(403, b"<html>Just a moment...</html>")
try: paladin_auth.login("https://h/gis/auth", "a", "b")
except paladin_auth.AuthError as e: assert e.code == "blocked", e.code
paladin_auth.urllib.request.urlopen = fake(403, b'{"error":"tier_insufficient","message":"no gis"}')
try: paladin_auth.login("https://h/gis/auth", "a", "b")
except paladin_auth.AuthError as e: assert e.code == "tier_insufficient"
print("7 user-agent + Cloudflare-vs-license 403 ok\nALL PASS")
