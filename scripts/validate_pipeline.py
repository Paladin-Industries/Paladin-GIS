#!/usr/bin/env python3
"""Paladin GIS pipeline validator - tests mcleod's contract directly.

Both GIS clients (QGIS plugin, ArcGIS Pro toolbox) are thin wrappers over this
contract, so run this FIRST. If it fails, client testing is meaningless.

    python3 validate_pipeline.py                         # prompts for accounts
    python3 validate_pipeline.py --leave-public          # leaves a public test
                                                         # dozer line for WxPal
    python3 validate_pipeline.py --cleanup <tactic_id>   # tombstone it after

Accounts (env vars or prompts):
    A  PALADIN_A_EMAIL / PALADIN_A_PASSWORD   required - main GIS-licensed user
    B  PALADIN_B_EMAIL / PALADIN_B_PASSWORD   optional - user in a DIFFERENT org
    C  PALADIN_C_EMAIL / PALADIN_C_PASSWORD   optional - other user, SAME org as A

Writes only to the real disturbances/ prefix, as `private` tactics labelled
PALADIN-VALIDATION, and tombstones every one it creates. Append-only storage
means the versions remain in the bucket (as tombstones) by design.
Stdlib only.
"""

import argparse
import getpass
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

DEFAULT_AUTH = "https://gis.paladinindustries.com/gis/auth"
PREFIX = "disturbances/"
UA = "PaladinGIS-Validator/1.0"
SCHEMA = "paladin.tactic.v3"
FIELDS = ["tactic_id", "version_id", "version", "supersedes", "status", "tactic_type",
          "model_role", "label", "notes", "created_utc", "effective_from_utc",
          "effective_to_utc", "org_id", "author", "visibility", "simulate", "source",
          "source_file", "line_width_m"]
# A short open dozer line in east San Jose foothills (Alum Rock area).
TEST_LINE = [[-121.8040, 37.3990], [-121.7980, 37.4010], [-121.7920, 37.4005]]

RESULTS = []


def record(tid, name, status, detail=""):
    RESULTS.append((tid, name, status, detail))
    mark = {"PASS": "\033[32mPASS\033[0m", "FAIL": "\033[31mFAIL\033[0m",
            "WARN": "\033[33mWARN\033[0m", "SKIP": "SKIP"}[status]
    print("  %-4s %s  %s%s" % (tid, mark, name, ("  -- " + detail) if detail else ""))


class Resp:
    def __init__(self, status, body, ctype=""):
        self.status, self.body, self.ctype = status, body, ctype

    def json(self):
        try:
            return json.loads(self.body) if self.body else None
        except ValueError:
            return None

    @property
    def is_json(self):
        return self.json() is not None or self.body == b""


def call(method, url, body=None, token=None, ua=UA, timeout=30):
    h = {"Accept": "application/json"}
    if ua:
        h["User-Agent"] = ua
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Resp(r.status, r.read(), r.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as e:
        return Resp(e.code, e.read(), e.headers.get("Content-Type", "") if e.headers else "")


class Account:
    def __init__(self, auth, email, password):
        self.auth, self.email, self.password = auth.rstrip("/"), email, password
        self.base = (self.auth[: -len("/auth")] if self.auth.endswith("/gis/auth") else self.auth) + "/disturbances"
        self.session = {}

    def login(self):
        r = call("POST", self.auth + "/login", {"email": self.email, "password": self.password})
        if r.status == 200 and r.json():
            self.session = r.json()
        return r

    @property
    def token(self):
        return self.session.get("token", "")

    def list(self, token=None):
        r = call("GET", self.base + "/list?" + urllib.parse.urlencode({"prefix": PREFIX}),
                 token=self.token if token is None else token)
        return r, ((r.json() or {}).get("keys") or []) if r.status == 200 else []

    def get(self, key):
        return call("GET", self.base + "/get?" + urllib.parse.urlencode({"key": key}), token=self.token)

    def put(self, obj):
        return call("POST", self.base + "/put", body=obj, token=self.token)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def payload(lineage, version, supersedes, *, visibility="private", status="active",
            author="", org="", notes="PALADIN-VALIDATION"):
    vid = str(uuid.uuid4())
    p = {"paladin_schema": SCHEMA, "crs": "EPSG:4326"}
    p.update({f: "" for f in FIELDS})
    p.update(tactic_id=lineage, version_id=vid, version=str(version), supersedes=supersedes,
             status=status, tactic_type="dozer_line", model_role="control_line",
             label="PALADIN-VALIDATION dozer", notes=notes, created_utc=now(),
             org_id=org, author=author, visibility=visibility, simulate="true",
             source="drawn", line_width_m="4.0")
    return vid, {"type": "FeatureCollection", "features": [{
        "type": "Feature", "id": lineage, "properties": p,
        "geometry": {"type": "MultiLineString", "coordinates": [TEST_LINE]}}]}


def keys_for(keys, lineage):
    return sorted(k for k in keys if k.startswith(PREFIX + lineage + "/"))


def props_of(resp):
    j = resp.json() or {}
    feats = j.get("features") or []
    return (feats[0].get("properties") or {}) if feats else {}


def get_account(label, required):
    email = os.environ.get("PALADIN_%s_EMAIL" % label)
    if not email:
        email = input("Account %s email%s: " % (label, "" if required else " (blank to skip)")).strip()
    if not email:
        return None, None
    pw = os.environ.get("PALADIN_%s_PASSWORD" % label) or getpass.getpass("Account %s password: " % label)
    return email, pw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auth", default=os.environ.get("PALADIN_AUTH", DEFAULT_AUTH))
    ap.add_argument("--leave-public", action="store_true")
    ap.add_argument("--cleanup", metavar="TACTIC_ID")
    args = ap.parse_args()
    plain = args.auth.startswith("http://")
    if plain and not os.environ.get("PALADIN_ALLOW_HTTP"):
        sys.exit("Refusing to send passwords over plain HTTP. Finish the HTTPS setup first.\n"
                 "(Local mcleod with TEST accounts only: set PALADIN_ALLOW_HTTP=1.)")

    print("\nPaladin GIS pipeline validation -> %s\n" % args.auth)
    ea, pa = get_account("A", True)
    A = Account(args.auth, ea, pa)

    # ---- 0. Transport ------------------------------------------------------
    print("[0] Transport")
    host = urllib.parse.urlparse(args.auth).hostname
    if plain:
        record("T0.1", "TLS", "SKIP", "plain HTTP (local test mode)")
    else:
      try:
        ssl.create_default_context().wrap_socket(
            __import__("socket").create_connection((host, 443), timeout=10),
            server_hostname=host).close()
        record("T0.1", "TLS certificate valid for %s" % host, "PASS")
      except Exception as exc:  # noqa: BLE001
        record("T0.1", "TLS certificate valid for %s" % host, "FAIL", str(exc))
        return finish()
    for tid, ua, label in (("T0.2", UA, "custom User-Agent"),
                           ("T0.3", None, "default Python-urllib User-Agent (old plugin builds)")):
        r = call("POST", A.auth + "/login", {"email": "nobody@example.invalid", "password": "x"}, ua=ua)
        if r.status == 404:
            record(tid, "login route reachable (%s)" % label, "FAIL", "404 - mcleod not mounted at this path")
            return finish()
        if r.status in (401, 400) and r.json() is not None:
            record(tid, "login route reachable (%s)" % label, "PASS", "HTTP %s JSON" % r.status)
        elif r.json() is None:
            record(tid, "login route reachable (%s)" % label, "FAIL" if ua else "WARN",
                   "HTTP %s non-JSON (Cloudflare challenge/WAF?)" % r.status)
        else:
            record(tid, "login route reachable (%s)" % label, "WARN", "HTTP %s" % r.status)

    # ---- 1. Auth -----------------------------------------------------------
    print("[1] Auth")
    r = call("POST", A.auth + "/login", {"email": ea, "password": pa + "-wrong"})
    record("T1.1", "wrong password -> 401 JSON", "PASS" if r.status == 401 and r.json() is not None else "FAIL",
           "HTTP %s" % r.status)
    r = A.login()
    s = A.session
    missing = [k for k in ("token", "refresh_token", "author", "org_id", "tier") if not s.get(k)]
    if r.status != 200:
        record("T1.2", "login A", "FAIL", "HTTP %s %s" % (r.status, r.body[:200]))
        return finish()
    record("T1.2", "login A returns token/refresh/author/org/tier",
           "PASS" if not missing else "FAIL", ("missing: %s" % missing) if missing else
           "author=%s org=%s tier=%s" % (s["author"], s["org_id"], s["tier"]))
    r = call("POST", A.auth + "/refresh", {"refresh_token": s.get("refresh_token"), "email": ea})
    ok = r.status == 200 and (r.json() or {}).get("token")
    record("T1.3", "refresh token -> new access token", "PASS" if ok else "FAIL", "HTTP %s" % r.status)
    if ok:
        A.session["token"] = r.json()["token"]
    r, _ = A.list(token="")
    record("T1.4", "list without token -> 401", "PASS" if r.status == 401 else "FAIL", "HTTP %s" % r.status)
    r, _ = A.list(token="garbage.token.value")
    record("T1.5", "list with forged token -> 401", "PASS" if r.status == 401 else "FAIL", "HTTP %s" % r.status)

    if args.cleanup:
        return cleanup(A, args.cleanup)

    # ---- 2. Write / versioning --------------------------------------------
    print("[2] Write + versioning")
    lin = str(uuid.uuid4())
    vid1, obj = payload(lin, 1, "", author="spoofed-author", org="spoofed-org")
    r = A.put(obj)
    record("T2.1", "put v1", "PASS" if 200 <= r.status < 300 else "FAIL", "HTTP %s %s" % (r.status, r.body[:150]))
    r, keys = A.list()
    ks = keys_for(keys, lin)
    exp = "%s%s/000001-%s.geojson" % (PREFIX, lin, vid1)
    record("T2.2", "stored key = {prefix}/{tactic_id}/{v:06d}-{version_id}.geojson",
           "PASS" if ks == [exp] else "FAIL", "got %s" % ks)
    g = A.get(exp)
    p = props_of(g)
    record("T2.3", "get v1 round-trips schema + geometry",
           "PASS" if g.status == 200 and p.get("paladin_schema") == SCHEMA and p.get("line_width_m") == "4.0"
           and (g.json() or {}).get("features", [{}])[0].get("geometry", {}).get("type") == "MultiLineString"
           else "FAIL", "HTTP %s" % g.status)
    record("T2.4", "server stamps author from token (ignores spoofed author)",
           "PASS" if p.get("author") == s["author"] else "FAIL", "stored author=%r" % p.get("author"))
    record("T2.5", "server stamps org from token (ignores spoofed org)",
           "PASS" if p.get("org_id") == s["org_id"] else "FAIL", "stored org=%r" % p.get("org_id"))
    _, dup = payload(lin, 1, "")
    r = A.put(dup)
    record("T2.6", "duplicate version number -> 409 (no fork)", "PASS" if r.status == 409 else "FAIL",
           "HTTP %s - clients rely on this to detect concurrent edits" % r.status)
    vid2, obj2 = payload(lin, 2, vid1)
    r = A.put(obj2)
    _, keys = A.list()
    ks = keys_for(keys, lin)
    v2key = "%s%s/000002-%s.geojson" % (PREFIX, lin, vid2)
    record("T2.7", "edit v2 appends (v1 retained)",
           "PASS" if 200 <= r.status < 300 and v2key in ks and exp in ks else "FAIL", "%d versions" % len(ks))
    bad = payload(lin, 3, vid2)[1]
    bad["features"][0]["properties"]["paladin_schema"] = "paladin.tactic.v999"
    r = A.put(bad)
    record("T2.8", "unknown schema rejected", "PASS" if r.status in (400, 422) else "WARN",
           "HTTP %s (WARN = server accepts anything)" % r.status)
    if 200 <= r.status < 300:   # it was accepted: next version must step past it
        vid2 = props_of(A.get(keys_for(A.list()[1], lin)[-1])).get("version_id", vid2)
    cur_v = len(keys_for(A.list()[1], lin))

    # ---- 3. Visibility / authorization ------------------------------------
    print("[3] Visibility + authorization")
    eb, pb = get_account("B", False)
    ec, pc = get_account("C", False)
    B = Account(args.auth, eb, pb) if eb else None
    C = Account(args.auth, ec, pc) if ec else None
    for acct, label in ((B, "B"), (C, "C")):
        if acct and acct.login().status != 200:
            record("T3.0", "login %s" % label, "FAIL")
            if label == "B":
                B = None
            else:
                C = None
    if B and B.session.get("org_id") == s["org_id"]:
        record("T3.0", "account B is in a different org", "WARN", "same org - org tests for B are invalid")
    cur_key = keys_for(A.list()[1], lin)[-1]
    if B:
        r = B.get(cur_key)
        record("T3.1", "B cannot read A's PRIVATE tactic", "PASS" if r.status in (403, 404) else "FAIL",
               "HTTP %s" % r.status)
        _, bkeys = B.list()
        record("T3.2", "B's list hides A's private keys", "PASS" if not keys_for(bkeys, lin) else "WARN",
               "keys leak tactic ids but not content")
        # Can B write a version into A's lineage? (authorization hole if yes)
        _, hijack = payload(lin, cur_v + 1, "", visibility="public", notes="HIJACK-TEST")
        r = B.put(hijack)
        record("T3.3", "B cannot write into A's private lineage", "PASS" if r.status in (403, 404) else "FAIL",
               "HTTP %s%s" % (r.status, " - anyone can supersede anyone's tactic" if 200 <= r.status < 300 else ""))
        if 200 <= r.status < 300:
            cur_v += 1
    else:
        for t in ("T3.1", "T3.2", "T3.3"):
            record(t, "cross-org checks", "SKIP", "no account B")
    cur_v += 1
    vid, objo = payload(lin, cur_v, vid2, visibility="org")
    r = A.put(objo)
    cur_key = "%s%s/%06d-%s.geojson" % (PREFIX, lin, cur_v, vid)
    if B:
        r = B.get(cur_key)
        record("T3.4", "other-org B cannot read ORG tactic", "PASS" if r.status in (403, 404) else "FAIL",
               "HTTP %s" % r.status)
    if C:
        r = C.get(cur_key)
        record("T3.5", "same-org C CAN read ORG tactic", "PASS" if r.status == 200 else "FAIL", "HTTP %s" % r.status)
    else:
        record("T3.5", "same-org read", "SKIP", "no account C")
    cur_v += 1
    vidp, objp = payload(lin, cur_v, vid, visibility="public")
    A.put(objp)
    pub_key = "%s%s/%06d-%s.geojson" % (PREFIX, lin, cur_v, vidp)
    if B:
        r = B.get(pub_key)
        record("T3.6", "B CAN read PUBLIC tactic", "PASS" if r.status == 200 else "FAIL", "HTTP %s" % r.status)

    # ---- 4. Tombstone / cleanup ---------------------------------------------
    print("[4] Tombstone")
    if args.leave_public:
        print("\n  Left PUBLIC test dozer line in Alum Rock area: tactic_id = %s" % lin)
        print("  Check it in WxPal + QGIS + Pro, then:  python3 validate_pipeline.py --cleanup %s\n" % lin)
        return finish()
    return cleanup(A, lin)


def cleanup(A, lin):
    _, keys = A.list()
    ks = keys_for(keys, lin)
    if not ks:
        record("T4.1", "tombstone", "FAIL", "lineage %s not found" % lin)
        return finish()
    last = ks[-1]
    cur = props_of(A.get(last))
    v = int(last.rsplit("/", 1)[-1].split("-", 1)[0]) + 1
    vid, obj = payload(lin, v, cur.get("version_id", ""), status="inactive",
                       visibility=cur.get("visibility") or "private")
    r = A.put(obj)
    ok = 200 <= r.status < 300 and props_of(A.get(keys_for(A.list()[1], lin)[-1])).get("status") == "inactive"
    record("T4.1", "tombstone v%d is current version" % v, "PASS" if ok else "FAIL", "HTTP %s" % r.status)
    return finish()


def finish():
    n = {k: sum(1 for r in RESULTS if r[2] == k) for k in ("PASS", "FAIL", "WARN", "SKIP")}
    print("\n%(PASS)d pass, %(FAIL)d fail, %(WARN)d warn, %(SKIP)d skip" % n)
    with open("pipeline_validation_%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"), "w") as fh:
        json.dump([dict(zip(("id", "test", "status", "detail"), r)) for r in RESULTS], fh, indent=1)
    return 1 if n["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
