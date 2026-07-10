"""Shared Cortex XDR auth/client helpers, used by both cortex_read and
cortex_actions tool modules.

Uses Cortex XDR's "Standard" auth scheme (static Authorization + x-xdr-auth-id
headers). If your API key was created with "Advanced" auth, these calls will
be rejected (401/403) and the headers need to be replaced with per-request
HMAC-SHA256 signing (nonce + timestamp + hash) instead — not implemented here
since we don't yet know which scheme this tenant uses.
"""

import os
import httpx


CORTEX_API_URL = os.environ.get("CORTEX_API_URL", "")  # e.g. https://api-<instance>.xdr.<region>.paloaltonetworks.com
CORTEX_API_KEY = os.environ.get("CORTEX_API_KEY", "")
CORTEX_API_KEY_ID = os.environ.get("CORTEX_API_KEY_ID", "")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=True, timeout=30)


def _headers() -> dict:
    return {
        "Authorization": CORTEX_API_KEY,
        "x-xdr-auth-id": CORTEX_API_KEY_ID,
        "Content-Type": "application/json",
    }
