"""Shared Check Point Management API auth/client helpers, used by the
checkpoint_read tool module.

Uses session-based auth: POST /web_api/login with user+password returns a
"sid" session token (session-timeout ~600s / 10 min per this tenant's real
response), used via the X-chkp-sid header on subsequent calls. TLS
verification is disabled to match this tenant's self-signed management
server certificate. Confirmed working against this tenant's real Check
Point Management Server.
"""

import os

import httpx


CHECKPOINT_MGMT_URL = os.environ.get("CHECKPOINT_MGMT_URL", "")  # e.g. https://<management-server-ip>
CHECKPOINT_USER = os.environ.get("CHECKPOINT_USER", "")
CHECKPOINT_PASS = os.environ.get("CHECKPOINT_PASS", "")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=False, follow_redirects=True, timeout=30)


async def _login(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f"{CHECKPOINT_MGMT_URL}/web_api/login",
        json={"user": CHECKPOINT_USER, "password": CHECKPOINT_PASS},
    )
    resp.raise_for_status()
    return resp.json().get("sid", "")
