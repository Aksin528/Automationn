"""Trellix DLP (on-prem, 11.x) read-only tools for SOC MCP server.

Deliberately separated from trellix_actions.py so this module can be
exposed as its own MCP integration (soc-mcp) and bound only to
investigative agents, while the state-changing tools in
trellix_actions.py are bound only to responder-agent via a separate
soc-mcp-actions integration. See cortex_read.py's module docstring for
the full rationale (same least-privilege split, same pattern).
"""

import time

from .trellix_common import TRELLIX_EPO_URL, _client, _auth


def register_trellix_read_tools(mcp):

    @mcp.tool()
    async def trellix_get_incident_ids(hours_back: int = 24, incident_nature: int = 1) -> dict:
        """List Trellix DLP incident IDs created in the last `hours_back`
        hours. incident_nature: 1 = data-in-use/motion (default, most
        common), 3 = data-at-rest network discovery. Returns up to 1000
        IDs per call; if more exist, the response's "endTime" is non-null
        (use it as the next call's implied start point — pagination beyond
        1000 isn't handled by this tool, call again with a smaller
        hours_back if you hit the cap).
        """
        start_ms = int((time.time() - hours_back * 3600) * 1000)
        url = f"{TRELLIX_EPO_URL}/rest/dlp/incidents/ids"
        params = {"startTime": start_ms, "incidentNature": incident_nature}
        async with _client() as client:
            resp = await client.get(url, params=params, auth=_auth())
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return resp.json()

    @mcp.tool()
    async def trellix_get_incident_details(incident_id: int, incident_nature: int = 1) -> dict:
        """Get full detail for one Trellix DLP incident: generalDetails
        (severity, status, action), endpointDetails, eventUserDetails,
        reportingProduct (matched policy), evidences, rules,
        classifications, existing comments, and linked cases.
        """
        url = f"{TRELLIX_EPO_URL}/rest/dlp/incidents/{incident_id}"
        params = {"incidentNature": incident_nature}
        async with _client() as client:
            resp = await client.get(url, params=params, auth=_auth())
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return resp.json()
