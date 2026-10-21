"""Check Point Management API (Mail Transfer Agent / Threat Prevention
blades) read-only tools for SOC MCP server.

show-logs is a query, not a state-changing call, so this lives in soc-mcp
(not soc-mcp-actions) alongside the other investigative tool modules.
"""

from datetime import UTC, datetime, timedelta

from .checkpoint_common import CHECKPOINT_MGMT_URL, _client, _login


def register_checkpoint_read_tools(mcp):

    @mcp.tool()
    async def checkpoint_get_mail_logs(
        blade: str,
        action: str,
        hours_back: int = 24,
        max_logs: int = 100,
    ) -> dict:
        """Query Check Point mail-security logs for a specific blade +
        action combination. Confirmed working combinations for blocked
        mail (against this tenant's real logs):
        - blade="Anti-Spam and Email Security", action="Reject"
        - blade="Threat Emulation", action="Prevent"
        - blade="Threat Extraction", action="Prevent"
        - blade="Anti-Virus", action="Prevent"

        Each log has a unique "id" field. Threat Emulation/Extraction/
        Anti-Virus logs carry rich email + file/URL forensic detail
        (subject, from/to, file name/hash, malware_family, calc_desc).
        The Anti-Spam blade does NOT carry subject/sender/recipient
        fields — confirmed empty against this tenant's real data, do not
        assume they exist.

        max_logs is capped at 100 (Check Point's own per-request limit).
        """
        now = datetime.now(UTC)
        start = now - timedelta(hours=hours_back)
        async with _client() as client:
            sid = await _login(client)
            resp = await client.post(
                f"{CHECKPOINT_MGMT_URL}/web_api/show-logs",
                headers={"X-chkp-sid": sid, "Content-Type": "application/json"},
                json={
                    "new-query": {
                        "filter": f'blade:"{blade}" AND action:{action}',
                        "time-frame": "custom",
                        "custom-start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "custom-end": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "max-logs-per-request": min(max_logs, 100),
                    }
                },
            )
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return resp.json()
