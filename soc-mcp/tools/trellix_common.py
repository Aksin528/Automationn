"""Shared Trellix DLP (on-prem, 11.x) auth/client helpers, used by both
trellix_read and trellix_actions tool modules.

Uses Basic Auth (ePO username + password) directly against the ePO
on-prem REST API (self-signed cert, TLS verification disabled to match
how this tenant's ePO server is deployed) — confirmed against this
tenant's real DLP 11.12.x instance.
"""

import os

import httpx


TRELLIX_EPO_URL = os.environ.get("TRELLIX_EPO_URL", "")  # e.g. https://<epo_server>:8443
TRELLIX_USER = os.environ.get("TRELLIX_USER", "")
TRELLIX_PASS = os.environ.get("TRELLIX_PASS", "")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=False, follow_redirects=True, timeout=30)


def _auth() -> tuple[str, str]:
    return (TRELLIX_USER, TRELLIX_PASS)
