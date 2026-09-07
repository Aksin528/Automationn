"""Splunk Enterprise Security state-changing tools for SOC MCP Actions
server.

Deliberately separated from splunk.py (read-only) so this module can be
exposed as its own MCP integration, matching the same read/write split
already used for Cortex (cortex_read.py / cortex_actions.py) and other
integrations in this project.

Uses ES's own notable_update REST endpoint -- confirmed against Splunk's
official Notable Event API reference (help.splunk.com):
https://help.splunk.com/en/splunk-enterprise-security-7/api-reference/7.3/notable-event-endpoints/notable-event-api-reference
"""

from .splunk import SPLUNK_BASE_URL, _client, _headers


def register_splunk_action_tools(mcp):

    @mcp.tool()
    async def splunk_update_notable_event(
        event_id: str,
        status: str = "",
        comment: str = "",
        urgency: str = "",
        new_owner: str = "",
    ) -> dict:
        """Update a Splunk Enterprise Security notable event: status,
        comment, urgency, and/or owner. Only the fields you pass are
        changed -- at least one of status/comment/urgency/new_owner is
        required.

        event_id: the notable event's own "event_id" field (same value
        used for dedup by the Splunk pull workflow) -- this is the
        ruleUID Splunk's notable_update API expects, shaped like
        "<UUID>@@notable@@<hash>".

        status: a numeric status ID as a string. These IDs are
        tenant-specific (defined in this Splunk instance's own
        reviewstatuses.conf) -- do not guess a value here without
        confirming it against this tenant's actual configured statuses
        first (e.g. via the Splunk web UI's notable event status
        dropdown, or reviewstatuses.conf directly).

        Only call this after explicit human approval -- this writes to a
        live Splunk ES notable event.
        """
        if not any([status, comment, urgency, new_owner]):
            return {
                "error": "At least one of status/comment/urgency/new_owner is required"
            }

        url = f"{SPLUNK_BASE_URL}/services/notable_update"
        data: dict = {"ruleUIDs": event_id}
        if status:
            data["status"] = status
        if comment:
            data["comment"] = comment
        if urgency:
            data["urgency"] = urgency
        if new_owner:
            data["newOwner"] = new_owner

        async with _client() as client:
            resp = await client.post(
                url,
                headers=_headers(),
                data=data,
                params={"output_mode": "json"},
            )
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return {"status": "update_requested", "event_id": event_id, "response": resp.json()}
