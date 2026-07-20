"""Trellix DLP (on-prem, 11.x) state-changing tools for SOC MCP server.

Deliberately separated from trellix_read.py so this module can be exposed
as its own MCP integration (soc-mcp-actions) and bound only to
responder-agent. See trellix_read.py's module docstring for the full
rationale.
"""

from .trellix_common import TRELLIX_EPO_URL, _client, _auth


def register_trellix_action_tools(mcp):

    @mcp.tool()
    async def trellix_set_status(incident_id: int, status_id: int, incident_nature: int = 1) -> dict:
        """Set a Trellix DLP incident's status. status_id options:
        2=NEW, 3=PENDING, 4=VIEWED, 5=UNDER_INVESTIGATION, 6=ESCALATED,
        7=RESOLVED, 8=FALSE_POSITIVE, 201=SUSPENDED, 202=ARCHIVED,
        203=OPENED, 204=SUPPRESSED. Only call after explicit human approval.
        """
        url = f"{TRELLIX_EPO_URL}/rest/dlp/incidents/setStatus/{incident_id}"
        params = {"incidentNature": incident_nature, "statusId": status_id}
        async with _client() as client:
            resp = await client.put(url, params=params, auth=_auth())
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return {"status": "status_updated", "incident_id": incident_id, "status_id": status_id}

    @mcp.tool()
    async def trellix_set_severity(incident_id: int, severity_id: int, incident_nature: int = 1) -> dict:
        """Set a Trellix DLP incident's severity. severity_id options:
        0=INFO, 1=WARNING, 2=MINOR, 3=MAJOR, 4=CRITICAL. Only call after
        explicit human approval.
        """
        url = f"{TRELLIX_EPO_URL}/rest/dlp/incidents/setSeverity/{incident_id}"
        params = {"incidentNature": incident_nature, "severityId": severity_id}
        async with _client() as client:
            resp = await client.put(url, params=params, auth=_auth())
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return {"status": "severity_updated", "incident_id": incident_id, "severity_id": severity_id}

    @mcp.tool()
    async def trellix_add_comment(incident_id: int, text: str, incident_nature: int = 1) -> dict:
        """Add a comment to a Trellix DLP incident. Unlike Cortex XDR,
        this is a genuine, native, supported comment API — confirmed
        working against this tenant. Only call after explicit human
        approval.
        """
        url = f"{TRELLIX_EPO_URL}/rest/dlp/incidents/comments/{incident_id}"
        params = {"incidentNature": incident_nature}
        async with _client() as client:
            resp = await client.put(
                url, params=params, json={"newComment": text}, auth=_auth()
            )
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return {"status": "comment_added", "incident_id": incident_id}
