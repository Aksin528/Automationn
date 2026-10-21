"""AlienVault OTX (Open Threat Exchange) tools for SOC MCP server.

Auth: the OTX API key is read from the incoming MCP request's `X-OTX-Key`
HTTP header (see virustotal_read.py for why -- resolved by Tracecat from
its Credentials store per call, not an env var).
"""

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

OTX_BASE_URL = "https://otx.alienvault.com/api/v1/indicators"


def _pulse_summary(indicator: str, pulse_info: dict) -> dict:
    pulses = pulse_info.get("pulses", [])
    count = pulse_info.get("count", 0)
    tags = sorted({tag for p in pulses for tag in p.get("tags", [])})
    return {
        "pulse_count": count,
        "pulse_names": [p.get("name") for p in pulses[:5]],
        "tags": tags[:10],
        "summary": (
            f"{indicator}: referenced in {count} OTX threat-intel pulse(s)"
            if count else f"{indicator}: not seen in any OTX pulse"
        ),
    }


def register_otx_tools(mcp: FastMCP):

    @mcp.tool()
    async def otx_lookup_ip(ip: str) -> dict:
        """Look up an IP address on AlienVault OTX (community threat-intel pulses).
        Use this to check if an IP is referenced in known threat campaigns/reports.
        """
        api_key = get_http_headers().get("x-otx-key")
        if not api_key:
            return {"ip": ip, "error": "OTX API key not configured in Tracecat Credentials"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{OTX_BASE_URL}/IPv4/{ip}/general",
                    headers={"X-OTX-API-KEY": api_key},
                )
                r.raise_for_status()
                data = r.json()
                return {"ip": ip, **_pulse_summary(ip, data.get("pulse_info", {}))}
        except httpx.HTTPStatusError as e:
            return {"ip": ip, "error": f"OTX HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"ip": ip, "error": str(e)}

    @mcp.tool()
    async def otx_lookup_domain(domain: str) -> dict:
        """Look up a domain on AlienVault OTX (community threat-intel pulses).
        Use this to check if a domain is referenced in known threat campaigns/reports.
        """
        api_key = get_http_headers().get("x-otx-key")
        if not api_key:
            return {"domain": domain, "error": "OTX API key not configured in Tracecat Credentials"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{OTX_BASE_URL}/domain/{domain}/general",
                    headers={"X-OTX-API-KEY": api_key},
                )
                r.raise_for_status()
                data = r.json()
                return {"domain": domain, **_pulse_summary(domain, data.get("pulse_info", {}))}
        except httpx.HTTPStatusError as e:
            return {"domain": domain, "error": f"OTX HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"domain": domain, "error": str(e)}

    @mcp.tool()
    async def otx_lookup_hash(file_hash: str) -> dict:
        """Look up a file hash on AlienVault OTX (community threat-intel pulses).
        Use this to check if a file hash is referenced in known threat campaigns/reports.
        """
        api_key = get_http_headers().get("x-otx-key")
        if not api_key:
            return {"hash": file_hash, "error": "OTX API key not configured in Tracecat Credentials"}
        return await lookup_hash(file_hash, api_key)


async def lookup_hash(file_hash: str, api_key: str) -> dict:
    """Core OTX hash lookup, reusable outside the @mcp.tool wrapper above
    (e.g. by the hash_lookup_all_sources composite tool)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{OTX_BASE_URL}/file/{file_hash}/general",
                headers={"X-OTX-API-KEY": api_key},
            )
            r.raise_for_status()
            data = r.json()
            return {"hash": file_hash, **_pulse_summary(file_hash, data.get("pulse_info", {}))}
    except httpx.HTTPStatusError as e:
        return {"hash": file_hash, "error": f"OTX HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"hash": file_hash, "error": str(e)}
