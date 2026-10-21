"""Shared Cortex XDR auth/client helpers, used by both cortex_read and
cortex_actions tool modules.

Uses Cortex XDR's "Advanced" auth scheme: a fresh nonce + millisecond
timestamp + SHA256(api_key + nonce + timestamp) is computed on every request
instead of sending the raw API key. Confirmed against this tenant's real API
key via the /api_keys/validate/ endpoint before wiring this in.
"""

import hashlib
import os
import secrets
import string
import time

import httpx


CORTEX_API_URL = os.environ.get("CORTEX_API_URL", "")  # e.g. https://api-<instance>.xdr.<region>.paloaltonetworks.com
CORTEX_API_KEY = os.environ.get("CORTEX_API_KEY", "")
CORTEX_API_KEY_ID = os.environ.get("CORTEX_API_KEY_ID", "")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=True, timeout=30)


def _headers() -> dict:
    nonce = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(64))
    timestamp = int(time.time() * 1000)
    auth_key = f"{CORTEX_API_KEY}{nonce}{timestamp}".encode("utf-8")
    api_key_hash = hashlib.sha256(auth_key).hexdigest()
    return {
        "x-xdr-timestamp": str(timestamp),
        "x-xdr-nonce": nonce,
        "x-xdr-auth-id": CORTEX_API_KEY_ID,
        "Authorization": api_key_hash,
        "Content-Type": "application/json",
    }
