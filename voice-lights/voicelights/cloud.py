"""Fetch the Meross device key from your Meross account.

The device key is the shared secret every local request is signed with. It is
set when a bulb is provisioned and is the one thing you cannot read off the
bulb itself. Signing in to the Meross cloud once retrieves it; after that this
module is never used again and control is entirely local.

Best-effort by nature: Meross has changed this endpoint several times and it
is the one part of this project that cannot be tested without a live account.
If it fails, README.md documents how to read the key out of the Meross app or
a Homebridge/Home Assistant config instead.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import string
import time
import urllib.error
import urllib.request
from typing import Any

SECRET = "23x17ahWarFH6w29"
DEFAULT_DOMAIN = "https://iotx-us.meross.com"
DOMAINS = {
    "us": "https://iotx-us.meross.com",
    "eu": "https://iotx-eu.meross.com",
    "ap": "https://iotx-ap.meross.com",
}

API_STATUS = {
    1001: "Wrong password.",
    1002: "Unknown account.",
    1003: "That account is not activated.",
    1004: "Wrong email or password.",
    1005: "Invalid email address.",
    1019: "Token expired.",
    1022: "Wrong account region.",
    1030: "This account needs an MFA code.",
    1200: "Token has been revoked.",
    1255: "Too many requests; wait a few minutes.",
}


class CloudError(RuntimeError):
    pass


def _nonce(length: int = 16) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def _post(domain: str, path: str, params: dict[str, Any], token: str = "") -> dict[str, Any]:
    timestamp = int(time.time() * 1000)
    nonce = _nonce()
    encoded = base64.b64encode(json.dumps(params).encode()).decode()
    signature = hashlib.md5(f"{SECRET}{timestamp}{nonce}{encoded}".encode()).hexdigest()
    body = json.dumps(
        {"params": encoded, "sign": signature, "timestamp": timestamp, "nonce": nonce}
    ).encode()

    headers = {
        "Content-Type": "application/json",
        "AppVersion": "4.4.0",
        "AppType": "MerossIOT",
        "AppLanguage": "EN",
        "User-Agent": "MerossIOT/0.4.6",
    }
    if token:
        headers["Authorization"] = f"Basic {token}"

    request = urllib.request.Request(f"{domain}{path}", data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise CloudError(f"Meross API returned HTTP {exc.code} for {path}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise CloudError(f"Could not reach the Meross API: {exc}") from exc
    except ValueError as exc:
        raise CloudError(f"Meross API sent a non-JSON reply: {exc}") from exc

    status = data.get("apiStatus")
    if status not in (0, None):
        message = API_STATUS.get(status, data.get("info") or "Unknown error")
        raise CloudError(f"Meross login failed ({status}): {message}")
    return data.get("data") or {}


def sign_in(
    email: str, password: str, region: str = "us", mfa_code: str | None = None
) -> dict[str, Any]:
    """Return {"key", "token", "userid", "domain"} for a Meross account."""
    domain = DOMAINS.get(region.lower(), DEFAULT_DOMAIN)
    params: dict[str, Any] = {
        "email": email,
        "password": hashlib.md5(password.encode()).hexdigest(),
        "accountCountryCode": region.upper() if len(region) == 2 else "",
        "encryption": 1,
        "agree": 0,
    }
    if mfa_code:
        params["mfaCode"] = mfa_code

    data = _post(domain, "/v1/Auth/signIn", params)
    # Meross may hand back a different regional domain to use from here on.
    data["domain"] = data.get("domain") or domain
    if not data.get("key"):
        raise CloudError("Signed in, but the response carried no device key.")
    return data


def device_list(session: dict[str, Any]) -> list[dict[str, Any]]:
    """List the devices on the account, so bulbs can be named automatically."""
    data = _post(session["domain"], "/v1/Device/devList", {}, token=session["token"])
    if isinstance(data, list):
        return data
    return data.get("deviceList") or []


def fetch(email: str, password: str, region: str = "us", mfa_code: str | None = None):
    """Sign in and return (key, [{"name", "uuid", "mac", "model", "online"}])."""
    session = sign_in(email, password, region, mfa_code)
    devices = []
    try:
        for entry in device_list(session):
            devices.append(
                {
                    "name": entry.get("devName", ""),
                    "uuid": entry.get("uuid", ""),
                    "mac": entry.get("macAddress", ""),
                    "model": entry.get("deviceType", ""),
                    "online": (entry.get("onlineStatus") == 1),
                }
            )
    except CloudError:
        # The key is the part that matters; a failed device list is survivable.
        pass
    return session["key"], devices
