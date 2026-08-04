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
"""S3 access with pure-stdlib SigV4 — no boto3, no GIS imports.

Trimmed from the QGIS plugin's s3_client.py: same signing, same error
surface, minus the QGIS logging and the alternate backends.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
import datetime

from . import sigv4


class SyncError(Exception):
    pass


class S3NativeBackend:
    def __init__(self, bucket, region, access_key, secret_key,
                 session_token=""):
        if not access_key or not secret_key:
            raise SyncError(
                "No AWS credentials. Run 'Configure Paladin Settings' and "
                "expand the Credentials category before saving.")
        self._bucket = bucket
        self._region = region
        self._ak = access_key
        self._sk = secret_key
        self._token = session_token or ""
        self._host = "%s.s3.%s.amazonaws.com" % (bucket, region)
        self._endpoint = "https://" + self._host

    def _send(self, method, key="", query=None, body=b""):
        query = query or {}
        now = datetime.datetime.now(datetime.timezone.utc)
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")

        canonical_uri = "/" + sigv4.uri_encode(key, encode_slash=False)
        canonical_qs = "&".join(
            "%s=%s" % (sigv4.uri_encode(k), sigv4.uri_encode(v))
            for k, v in sorted(query.items()))
        payload_hash = sigv4.sha256_hex(body)

        headers = {"host": self._host,
                   "x-amz-content-sha256": payload_hash,
                   "x-amz-date": amzdate}
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
        """All object keys under a prefix, following continuation tokens."""
        keys = []
        token = None
        while True:
            query = {"list-type": "2", "prefix": prefix or ""}
            if token:
                query["continuation-token"] = token
            body = self._send("GET", key="", query=query).decode("utf-8")
            for chunk in body.split("<Key>")[1:]:
                keys.append(chunk.split("</Key>", 1)[0])
            if "<IsTruncated>true</IsTruncated>" in body:
                start = body.find("<NextContinuationToken>")
                if start == -1:
                    break
                token = body[start + len("<NextContinuationToken>"):
                             body.find("</NextContinuationToken>")]
            else:
                break
        return keys


def make_backend(settings):
    return S3NativeBackend(
        settings.get("bucket", schema_default("bucket")),
        settings.get("region", schema_default("region")),
        settings.get("access_key", ""), settings.get("secret_key", ""),
        settings.get("session_token", ""))


def schema_default(which):
    from . import schema
    return {"bucket": schema.DEFAULT_S3_BUCKET,
            "region": schema.DEFAULT_S3_REGION}[which]
