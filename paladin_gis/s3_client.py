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
"""Sync backends for pushing tactics up and reading disturbances down.

Every backend implements the same tiny interface:

    put_geojson(key, obj)          -> upload one GeoJSON dict at `key`
    list_keys(prefix)              -> list object keys under `prefix`
    get_geojson(key)               -> download + parse one GeoJSON object

Backends (config.DEFAULT_S3_BACKEND selects one):

  * S3NativeBackend (default): talks to S3 directly using AWS SigV4 signing
    implemented in pure standard library (see sigv4.py). NO third-party
    dependency — nothing for the customer to pip-install. Credentials are the
    Access Key ID + Secret entered in Settings.

  * Boto3Backend (optional): same, via boto3 if it happens to be installed.

  * PresignedBackend (future): brokers through a Paladin API so no AWS keys live
    on the client. This is the token -> API -> MongoDB path.
"""

import datetime
import json
import urllib.error
import urllib.parse
import urllib.request

from qgis.core import Qgis, QgsMessageLog

from . import config, paladin_auth


def _log(msg, level=Qgis.MessageLevel.Info):
    QgsMessageLog.logMessage(str(msg), "Paladin", level)


class SyncError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Native backend  (default) — SigV4 over stdlib, no third-party dependency
# --------------------------------------------------------------------------- #
class S3NativeBackend:
    def __init__(self, bucket, region, access_key, secret_key, session_token=""):
        if not (access_key and secret_key):
            raise SyncError(
                "No AWS credentials. Enter your Access Key ID and Secret "
                "Access Key in the plugin's Settings.")
        self._bucket = bucket
        self._region = region
        self._ak = access_key
        self._sk = secret_key
        self._token = session_token or ""
        # Virtual-hosted-style endpoint (bucket has no dots, so TLS is fine).
        self._host = "%s.s3.%s.amazonaws.com" % (bucket, region)
        self._endpoint = "https://%s" % self._host

    def _send(self, method, key="", query=None, body=b""):
        from . import sigv4
        query = query or {}
        now = datetime.datetime.now(datetime.timezone.utc)
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")

        canonical_uri = "/" + sigv4.uri_encode(key, encode_slash=False)
        canonical_qs = "&".join(
            "%s=%s" % (sigv4.uri_encode(k), sigv4.uri_encode(v))
            for k, v in sorted(query.items()))
        payload_hash = sigv4.sha256_hex(body)

        headers = {
            "host": self._host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amzdate,
        }
        if self._token:
            headers["x-amz-security-token"] = self._token

        authorization, _ = sigv4.sign(
            method, canonical_uri, canonical_qs, headers, payload_hash,
            amzdate, datestamp, self._region, self._ak, self._sk)

        url = self._endpoint + canonical_uri
        if canonical_qs:
            url += "?" + canonical_qs
        req_headers = dict(headers)
        req_headers["Authorization"] = authorization
        data = body if method in ("PUT", "POST") else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise SyncError("S3 %s %s -> HTTP %s: %s"
                            % (method, key or "/", exc.code, detail))
        except urllib.error.URLError as exc:
            raise SyncError("S3 %s %s failed: %s" % (method, key or "/", exc))

    def put_geojson(self, key, obj):
        self._send("PUT", key=key, body=json.dumps(obj).encode("utf-8"))

    def get_geojson(self, key):
        return json.loads(self._send("GET", key=key).decode("utf-8"))

    def list_keys(self, prefix):
        import xml.etree.ElementTree as ET
        ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
        keys, token = [], None
        while True:
            query = {"list-type": "2", "prefix": prefix}
            if token:
                query["continuation-token"] = token
            root = ET.fromstring(self._send("GET", key="", query=query))
            for c in root.findall("%sContents" % ns):
                k = c.findtext("%sKey" % ns)
                if k and k.endswith(".geojson"):
                    keys.append(k)
            if root.findtext("%sIsTruncated" % ns) == "true":
                token = root.findtext("%sNextContinuationToken" % ns)
            else:
                break
        return keys


# --------------------------------------------------------------------------- #
# Presigned backend  (future)
# --------------------------------------------------------------------------- #
class PresignedBackend:
    """Brokers all S3 access through a Paladin API.

    Expected Paladin endpoints (adjust paths to match the real API):
        POST {base}/gis/presign
             body: {"op": "put"|"get", "key": "..."}
             resp: {"url": "<presigned>", "headers": {...}}
        GET  {base}/gis/list?prefix=...
             resp: {"keys": ["...", ...]}

    Auth token (Cognito/JWT) is sent as Bearer.
    """

    def __init__(self, api_base, token=""):
        self._base = api_base.rstrip("/")
        self._token = token

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self._token:
            h["Authorization"] = "Bearer %s" % self._token
        return h

    def _presign(self, op, key):
        req = urllib.request.Request(
            self._base + "/gis/presign",
            data=json.dumps({"op": op, "key": key}).encode("utf-8"),
            headers=self._headers(), method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.URLError as exc:
            raise SyncError("presign %s failed: %s" % (op, exc))

    def put_geojson(self, key, obj):
        info = self._presign("put", key)
        body = json.dumps(obj).encode("utf-8")
        headers = info.get("headers", {}) or {}
        headers.setdefault("Content-Type", "application/geo+json")
        req = urllib.request.Request(info["url"], data=body,
                                     headers=headers, method="PUT")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                if resp.status not in (200, 201):
                    raise SyncError("PUT %s -> HTTP %s" % (key, resp.status))
        except urllib.error.URLError as exc:
            raise SyncError("upload %s failed: %s" % (key, exc))

    def list_keys(self, prefix):
        url = self._base + "/gis/list?" + urllib.parse.urlencode({"prefix": prefix})
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp).get("keys", [])
        except urllib.error.URLError as exc:
            raise SyncError("list %s failed: %s" % (prefix, exc))

    def get_geojson(self, key):
        info = self._presign("get", key)
        req = urllib.request.Request(info["url"], headers=info.get("headers", {}) or {})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.URLError as exc:
            raise SyncError("download %s failed: %s" % (key, exc))


# --------------------------------------------------------------------------- #
# boto3 backend  (internal / dev)
# --------------------------------------------------------------------------- #
class Boto3Backend:
    def __init__(self, bucket, region, access_key="", secret_key="",
                 session_token=""):
        try:
            import boto3  # noqa: F401
        except ImportError:
            raise SyncError(
                "boto3 is not installed in the QGIS Python environment.\n"
                "Install it with the QGIS Python (e.g. from the OSGeo4W shell on "
                "Windows, or the QGIS Python.app on macOS):\n"
                "    python -m pip install boto3\n"
                "Then restart QGIS."
            )
        import boto3
        self._bucket = bucket
        # If explicit keys were provided (the emailed customer credentials), use
        # them. Otherwise fall back to the local AWS credential chain
        # (env / ~/.aws / instance profile) for internal use.
        if access_key and secret_key:
            self._s3 = boto3.client(
                "s3", region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                aws_session_token=(session_token or None),
            )
        else:
            self._s3 = boto3.client("s3", region_name=region)

    def put_geojson(self, key, obj):
        try:
            self._s3.put_object(
                Bucket=self._bucket, Key=key,
                Body=json.dumps(obj).encode("utf-8"),
                ContentType="application/geo+json",
            )
        except Exception as exc:  # noqa: BLE001 - botocore raises many types
            raise SyncError("upload %s failed: %s" % (key, exc))

    def list_keys(self, prefix):
        keys = []
        try:
            paginator = self._s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    if item["Key"].endswith(".geojson"):
                        keys.append(item["Key"])
        except Exception as exc:  # noqa: BLE001
            raise SyncError("list %s failed: %s" % (prefix, exc))
        return keys

    def get_geojson(self, key):
        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=key)
            return json.loads(resp["Body"].read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise SyncError("download %s failed: %s" % (key, exc))


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
class McleodBackend:
    """Sync through the mcleod public API (JWT-gated, keyless).

    Same put/list/get interface as the S3 backends, so SyncWorker is unchanged.
    mcleod owns the S3 keys, stamps author/org from the verified token on write,
    and enforces visibility on read — so a `get` of an object you're not allowed
    to see returns 403, which we treat as "skip", not a fatal sync error.

    One URL to configure: the broker base is derived from the auth endpoint
    (`.../gis/auth` -> `.../gis/disturbances`).
    """

    def __init__(self, auth_endpoint, token, refresh_token="", email=""):
        self._auth_endpoint = auth_endpoint.rstrip("/")
        base = self._auth_endpoint
        if base.endswith("/gis/auth"):
            base = base[: -len("/auth")] + "/disturbances"
        else:
            base = base + "/disturbances"
        self._base = base
        self._token = token
        self._refresh = refresh_token
        self._email = email

    def _headers(self, extra=None):
        h = {"Authorization": "Bearer %s" % self._token}
        if extra:
            h.update(extra)
        return h

    @staticmethod
    def _expired():
        return SyncError("Session expired - sign in again in the Paladin Account panel.")

    def _refresh_access(self):
        """Trade the stored refresh token for a fresh access token, persist it,
        and return True on success. Called automatically on a 401."""
        if not self._refresh or not self._email:
            return False
        try:
            res = paladin_auth.refresh(self._auth_endpoint, self._refresh, self._email)
        except paladin_auth.AuthError:
            return False
        tok = res.get("token", "")
        if not tok:
            return False
        self._token = tok
        self._refresh = res.get("refresh_token", self._refresh)
        try:  # persist so the next session/sync starts with the fresh token
            from qgis.core import QgsSettings
            st = QgsSettings()
            st.beginGroup(config.SETTINGS_GROUP)
            st.setValue("token", self._token)
            st.setValue("refresh_token", self._refresh)
            st.endGroup()
        except Exception:
            pass
        return True

    def _send(self, method, url, body=None, _retried=False):
        """One request with the bearer token. On 401, refresh once and retry;
        if that still 401s, raise 'session expired'. Other HTTP errors are
        re-raised for the caller to interpret (403/404/409)."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        extra = {"Content-Type": "application/json"} if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(extra),
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                if not _retried and self._refresh_access():
                    return self._send(method, url, body, _retried=True)
                raise self._expired()
            raise
        except urllib.error.URLError as exc:
            raise SyncError("%s failed: %s" % (method, exc))

    def list_keys(self, prefix):
        url = self._base + "/list?" + urllib.parse.urlencode({"prefix": prefix})
        try:
            return (self._send("GET", url) or {}).get("keys", [])
        except urllib.error.HTTPError as exc:
            raise SyncError("list failed: HTTP %s" % exc.code)

    def get_geojson(self, key):
        url = self._base + "/get?" + urllib.parse.urlencode({"key": key})
        try:
            return self._send("GET", url)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                return None            # not visible / gone -> skip
            raise SyncError("get %s failed: HTTP %s" % (key, exc.code))

    def put_geojson(self, key, obj):
        try:
            self._send("POST", self._base + "/put", body=obj)
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return                 # version already stored (idempotent)
            raise SyncError("put %s failed: HTTP %s" % (key, exc.code))


def make_backend(settings):
    """Build the sync backend. All sync now goes through the mcleod broker,
    keyless — the plugin holds only the login endpoint + a token. Signing in is
    required; there is no direct-S3 path anymore (no AWS keys on the client)."""
    if settings.get("auth_endpoint") and settings.get("token"):
        return McleodBackend(
            settings["auth_endpoint"], settings["token"],
            settings.get("refresh_token", ""), settings.get("email", ""))
    raise SyncError(
        "Sign in to your Paladin account (Paladin Account panel) before syncing.")
