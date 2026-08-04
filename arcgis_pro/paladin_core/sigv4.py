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
"""AWS Signature Version 4 signing — pure standard library, no dependencies.

Just enough to sign S3 REST requests (PutObject, ListObjectsV2, GetObject) so
the plugin needs no boto3 / botocore install. Validated against AWS's published
"GET Object" example (see _selftest); run `python -m paladin_gis.sigv4`.
"""

import hashlib
import hmac
import urllib.parse


def uri_encode(value, encode_slash=True):
    """RFC3986 encode. Python's quote never escapes A-Za-z0-9-_.~, which is
    exactly the AWS unreserved set. `encode_slash=False` preserves '/' for the
    canonical path."""
    safe = "" if encode_slash else "/"
    return urllib.parse.quote(str(value), safe=safe)


def _hmac(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret, datestamp, region, service):
    k = _hmac(("AWS4" + secret).encode("utf-8"), datestamp)
    k = _hmac(k, region)
    k = _hmac(k, service)
    return _hmac(k, "aws4_request")


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def sign(method, canonical_uri, canonical_qs, headers, payload_hash,
         amzdate, datestamp, region, access_key, secret_key, service="s3"):
    """Return (authorization_header, signature_hex)."""
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(
        "%s:%s\n" % (h, headers[h].strip()) for h in sorted(headers))
    canonical_request = "\n".join([
        method, canonical_uri, canonical_qs,
        canonical_headers, signed_headers, payload_hash,
    ])
    scope = "%s/%s/%s/aws4_request" % (datestamp, region, service)
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amzdate, scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    sig = hmac.new(
        signing_key(secret_key, datestamp, region, service),
        string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
        % (access_key, scope, signed_headers, sig))
    return authorization, sig


def _selftest():
    """AWS docs 'GET Object' example — known-good signature."""
    empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    headers = {
        "host": "examplebucket.s3.amazonaws.com",
        "range": "bytes=0-9",
        "x-amz-content-sha256": empty,
        "x-amz-date": "20130524T000000Z",
    }
    _, sig = sign("GET", "/test.txt", "", headers, empty,
                  "20130524T000000Z", "20130524", "us-east-1",
                  "AKIAIOSFODNN7EXAMPLE",
                  "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    expected = "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    if sig != expected:
        raise AssertionError("SigV4 mismatch: got %s" % sig)
    return True


if __name__ == "__main__":
    print("SigV4 self-test:", "PASS" if _selftest() else "FAIL")
