"""Cortex XDR state-changing / response-action tools for SOC MCP server.

Deliberately separated from cortex_read.py so this module can be exposed as
its own MCP integration (soc-mcp-actions) and bound only to responder-agent.
See cortex_read.py's module docstring for why this split exists.
"""

from .cortex_common import CORTEX_API_URL, _client, _headers


def register_cortex_action_tools(mcp):

    @mcp.tool()
    async def cortex_isolate_endpoint(endpoint_id: str, incident_id: str = "") -> dict:
        """Isolate a Cortex XDR endpoint from the network. Requires the
        endpoint_id (use cortex_get_endpoint_by_ip first if you only have an
        IP). This is a real, irreversible-until-undone containment action —
        only call this after explicit human approval.
        """
        url = f"{CORTEX_API_URL}/public_api/v1/endpoints/isolate"
        request_data = {"endpoint_id": endpoint_id}
        if incident_id:
            request_data["incident_id"] = incident_id
        payload = {"request_data": request_data}
        async with _client() as client:
            resp = await client.post(url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return {"status": "isolation_requested", "endpoint_id": endpoint_id, "response": resp.json()}

    @mcp.tool()
    async def cortex_unisolate_endpoint(endpoint_id: str) -> dict:
        """Reverse isolation on a Cortex XDR endpoint (bring it back onto the
        network). Use this to undo a mistaken or no-longer-needed isolation.
        """
        url = f"{CORTEX_API_URL}/public_api/v1/endpoints/unisolate"
        payload = {"request_data": {"endpoint_id": endpoint_id}}
        async with _client() as client:
            resp = await client.post(url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return {"status": "unisolation_requested", "endpoint_id": endpoint_id, "response": resp.json()}

    @mcp.tool()
    async def cortex_update_incident(
        incident_id: str,
        status: str = "",
        severity: str = "",
        assigned_user_mail: str = "",
        comment: str = "",
    ) -> dict:
        """Update a Cortex XDR incident's status, severity, assignee, and/or
        add a resolution comment. Only the fields you pass are changed. This
        writes to a live incident — only call after explicit human approval.
        """
        update_data = {}
        if status:
            update_data["status"] = status
        if severity:
            update_data["manual_severity"] = severity
        if assigned_user_mail:
            update_data["assigned_user_mail"] = assigned_user_mail
        if comment:
            update_data["resolve_comment"] = comment
        url = f"{CORTEX_API_URL}/public_api/v1/incidents/update_incident"
        payload = {
            "request_data": {
                "incident_id": incident_id,
                "update_data": update_data,
            }
        }
        async with _client() as client:
            resp = await client.post(url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return {"status": "update_requested", "incident_id": incident_id, "response": resp.json()}

    @mcp.tool()
    async def cortex_scan_endpoint(endpoint_id: str) -> dict:
        """Trigger a full AV scan on a Cortex XDR endpoint. Use
        cortex_get_endpoint_by_ip first if you only have an IP.
        """
        url = f"{CORTEX_API_URL}/public_api/v1/endpoints/scan"
        payload = {"request_data": {"filters": [
            {"field": "endpoint_id_list", "operator": "in", "value": [endpoint_id]}
        ]}}
        async with _client() as client:
            resp = await client.post(url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return {"status": "scan_requested", "endpoint_id": endpoint_id, "response": resp.json()}

    @mcp.tool()
    async def cortex_quarantine_file(endpoint_id: str, file_path: str, file_hash: str, incident_id: str = "") -> dict:
        """Quarantine a specific file (by path + SHA256 hash) on a Cortex XDR
        endpoint. This is a real, irreversible-until-restored containment
        action — only call this after explicit human approval.
        """
        request_data: dict = {
            "filters": [
                {"field": "endpoint_id_list", "operator": "in", "value": [endpoint_id]}
            ],
            "file_path": file_path,
            "file_hash": file_hash,
        }
        if incident_id:
            request_data["incident_id"] = incident_id
        url = f"{CORTEX_API_URL}/public_api/v1/endpoints/quarantine"
        payload = {"request_data": request_data}
        async with _client() as client:
            resp = await client.post(url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return {"status": "quarantine_requested", "endpoint_id": endpoint_id, "response": resp.json()}

    @mcp.tool()
    async def cortex_blocklist_hash(file_hash: str, comment: str = "") -> dict:
        """Add a SHA256 file hash to the Cortex XDR global blocklist, so it's
        blocked from executing across all endpoints. This is a tenant-wide
        prevention policy change — only call after explicit human approval.
        """
        url = f"{CORTEX_API_URL}/public_api/v1/hash_exceptions/blocklist"
        request_data: dict = {"hash_list": [file_hash]}
        if comment:
            request_data["comment"] = comment
        payload = {"request_data": request_data}
        async with _client() as client:
            resp = await client.post(url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return {"status": "blocklist_requested", "hash": file_hash, "response": resp.json()}

    @mcp.tool()
    async def cortex_allowlist_hash(file_hash: str, comment: str = "") -> dict:
        """Add a SHA256 file hash to the Cortex XDR global allowlist, so it's
        exempt from prevention across all endpoints. Use to undo a false
        positive or a mistaken blocklist entry. This is a tenant-wide policy
        change — only call after explicit human approval.
        """
        url = f"{CORTEX_API_URL}/public_api/v1/hash_exceptions/allowlist"
        request_data: dict = {"hash_list": [file_hash]}
        if comment:
            request_data["comment"] = comment
        payload = {"request_data": request_data}
        async with _client() as client:
            resp = await client.post(url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return {"status": "allowlist_requested", "hash": file_hash, "response": resp.json()}
