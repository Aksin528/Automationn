"""AbuseIPDB threat intelligence tools for SOC MCP server.

Auth: the AbuseIPDB API key is read from the incoming MCP request's
`X-AbuseIPDB-Key` HTTP header (see virustotal_read.py for why -- resolved
by Tracecat from its Credentials store per call, not an env var).
"""

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

ABUSEIPDB_URL = "https://api.abuseipdb.com/api/v2/check"


def register_abuseipdb_tools(mcp: FastMCP):

    @mcp.tool()
    async def abuseipdb_check_ip(ip: str) -> dict:
        """Check an IP address's abuse history on AbuseIPDB (community-reported abuse).
        Returns an abuse confidence score (0-100) and recent report count.
        Use this to check if an IP has been reported for brute-force, scanning, spam, etc.
        """
        api_key = get_http_headers().get("x-abuseipdb-key")
        if not api_key:
            return {"ip": ip, "error": "AbuseIPDB API key not configured in Tracecat Credentials"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    ABUSEIPDB_URL,
                    headers={"Key": api_key, "Accept": "application/json"},
                    params={"ipAddress": ip, "maxAgeInDays": 90},
                )
                r.raise_for_status()
                data = r.json().get("data", {})
                score = data.get("abuseConfidenceScore", 0)
                reports = data.get("totalReports", 0)
                return {
                    "ip": ip,
                    "abuse_confidence_score": score,
                    "total_reports": reports,
                    "is_tor": data.get("isTor", False),
                    "is_whitelisted": data.get("isWhitelisted"),
                    "country_code": data.get("countryCode", "Unknown"),
                    "isp": data.get("isp", "Unknown"),
                    "domain": data.get("domain", "Unknown"),
                    "usage_type": data.get("usageType", "Unknown"),
                    "last_reported_at": data.get("lastReportedAt"),
                    "summary": (
                        f"{ip}: abuse score {score}/100 ({reports} reports)"
                        if reports else f"{ip}: no abuse reports"
                    ),
                }
        except httpx.HTTPStatusError as e:
            return {"ip": ip, "error": f"AbuseIPDB HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"ip": ip, "error": str(e)}
