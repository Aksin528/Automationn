"""Cortex XDR read-only tools for SOC MCP server.

Deliberately separated from cortex_actions.py so this module can be exposed
as its own MCP integration (soc-mcp) and bound only to investigative agents
(e.g. investigator-agent), while the destructive/state-changing tools in
cortex_actions.py are bound only to responder-agent via a separate
soc-mcp-actions integration. Tracecat's agent presets bind whole MCP servers,
not individual tools within one, so this split is what actually enforces
least privilege — not just prompt instructions.
"""

from .cortex_common import CORTEX_API_URL, _client, _headers


def register_cortex_read_tools(mcp):

    @mcp.tool()
    async def cortex_get_endpoint_by_ip(ip: str) -> dict:
        """Look up a Cortex XDR endpoint by its IP address. Returns the
        endpoint_id needed for response actions, plus hostname and agent
        status. Returns an empty 'endpoints' list if no match found.
        """
        url = f"{CORTEX_API_URL}/public_api/v1/endpoints/get_endpoints"
        payload = {
            "request_data": {
                "filters": [
                    {"field": "ip_list", "operator": "in", "value": [ip]}
                ]
            }
        }
        async with _client() as client:
            resp = await client.post(url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        data = resp.json()
        endpoints = data.get("reply", {}).get("data", [])
        return {
            "endpoints": [
                {
                    "endpoint_id": e.get("agent_id"),
                    "host_name": e.get("host_name"),
                    "ip": e.get("ip"),
                    "agent_status": e.get("agent_status"),
                    "operational_status": e.get("operational_status"),
                }
                for e in endpoints
            ]
        }

    @mcp.tool()
    async def cortex_get_incidents(status: str = "", limit: int = 20) -> dict:
        """List Cortex XDR incidents, optionally filtered by status
        (new, under_investigation, resolved_threat_handled,
        resolved_known_issue, resolved_duplicate, resolved_false_positive,
        resolved_other, resolved_auto). Sorted newest-first by modification
        time. Use cortex_get_incident_extra_data for full detail on one.
        """
        url = f"{CORTEX_API_URL}/public_api/v1/incidents/get_incidents"
        request_data: dict = {
            "sort": {"field": "modification_time", "keyword": "desc"},
            "search_from": 0,
            "search_to": limit,
        }
        if status:
            request_data["filters"] = [
                {"field": "status", "operator": "eq", "value": status}
            ]
        payload = {"request_data": request_data}
        async with _client() as client:
            resp = await client.post(url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return resp.json()

    @mcp.tool()
    async def cortex_get_incident_extra_data(incident_id: str, alerts_limit: int = 100) -> dict:
        """Get full detail for one Cortex XDR incident: alerts, network
        artifacts, file artifacts, and other enrichment beyond what
        cortex_get_incidents returns.
        """
        url = f"{CORTEX_API_URL}/public_api/v1/incidents/get_incident_extra_data"
        payload = {
            "request_data": {
                "incident_id": incident_id,
                "alerts_limit": alerts_limit,
            }
        }
        async with _client() as client:
            resp = await client.post(url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return resp.json()
