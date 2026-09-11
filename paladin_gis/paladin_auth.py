# paladin_auth.py
#
# Thin client for the Paladin GIS auth endpoint (mcleod / paradise
# /gis/auth/*). Dependency-free (stdlib urllib) to match the plugin's
# no-dependency policy. All calls are blocking; run them off the UI thread
# (see LoginWorker in panel.py).
#
# The endpoint authenticates email+password against Cognito, checks the user's
# license tier, and returns identity (+ AWS creds once the Identity Pool is on).
# A tier that isn't licensed for GIS comes back as HTTP 403 / code
# "tier_insufficient" so the UI can show "contact sales for GIS upgrade".

import json
import urllib.request
import urllib.error


class AuthError(Exception):
    def __init__(self, message, status=0, code="error"):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


_FRIENDLY = {
    400: "Missing email or password.",
    401: "Login failed — check your email and password.",
    403: "Your Paladin account isn't licensed for the GIS tool.",
    429: "Too many attempts. Wait a minute and try again.",
    500: "The login service had an error. Try again shortly.",
}


def _post(url, payload, timeout=20):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        j = {}
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    j = parsed
            except ValueError:
                j = {}
        raise AuthError(
            j.get("message") or _FRIENDLY.get(e.code, "Login failed (HTTP %s)." % e.code),
            status=e.code, code=j.get("error") or "http_error")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        raise AuthError("Couldn't reach the login service (%s)." % reason,
                        status=0, code="network")
    except (TimeoutError, OSError) as e:
        raise AuthError("Couldn't reach the login service (%s)." % e,
                        status=0, code="network")


def login(endpoint, email, password, timeout=20):
    """POST /login. Returns the session dict, or raises AuthError.
    On success the dict has: author, org_id, email, tier, expires_in,
    refresh_token, region, bucket, aws (aws is None until the Identity Pool
    is configured)."""
    url = endpoint.rstrip("/") + "/login"
    return _post(url, {"email": email, "password": password}, timeout)


def refresh(endpoint, refresh_token, email, timeout=20):
    """POST /refresh using the stored refresh token (not the password)."""
    url = endpoint.rstrip("/") + "/refresh"
    return _post(url, {"refresh_token": refresh_token, "email": email}, timeout)
