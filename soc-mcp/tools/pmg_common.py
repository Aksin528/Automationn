"""Shared Proxmox Mail Gateway (PMG) API auth helper, used by the pmg_read
tool module.

Uses Proxmox-style ticket auth: POST /api2/json/access/ticket with
username+password returns a "ticket" used via the PMGAuthCookie cookie on
subsequent GET calls (ticket lifetime ~2 hours per Proxmox convention). TLS
verification is disabled to match this tenant's self-signed management
server certificate. Confirmed working against this tenant's real PMG server.
"""

import os

import httpx


PMG_URL = os.environ.get("PMG_URL", "")  # e.g. https://<pmg-management-ip>:8006
PMG_USER = os.environ.get("PMG_USER", "")  # e.g. root@pam
PMG_PASS = os.environ.get("PMG_PASS", "")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=False, follow_redirects=True, timeout=30)


async def _login(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f"{PMG_URL}/api2/json/access/ticket",
        data={"username": PMG_USER, "password": PMG_PASS},
    )
    resp.raise_for_status()
    return resp.json().get("data", {}).get("ticket", "")


async def _get_node(client: httpx.AsyncClient, ticket: str) -> str:
    resp = await client.get(
        f"{PMG_URL}/api2/json/nodes",
        cookies={"PMGAuthCookie": ticket},
    )
    resp.raise_for_status()
    nodes = resp.json().get("data", [])
    return nodes[0]["node"] if nodes else ""
