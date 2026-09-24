"""VirusTotal threat intelligence tools for SOC MCP server.

Auth: the VirusTotal API key is NOT read from an environment variable like
the other tool modules in this server. It is expected on the incoming MCP
request as the `X-VirusTotal-Key` HTTP header, resolved by Tracecat from
its own Credentials store on every call (MCP integration "Custom" auth
type) -- so the key lives in Tracecat's encrypted secrets, not in this
container's env vars.
"""

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

VT_BASE_URL = "https://www.virustotal.com/api/v3"


def _api_key() -> str | None:
    return get_http_headers().get("x-virustotal-key")


def _stats_summary(subject: str, stats: dict) -> str:
    if not stats:
        return f"{subject}: no analysis data"
    malicious = stats.get("malicious", 0)
    suspicious = stats.get("suspicious", 0)
    total = sum(stats.values()) or 1
    if malicious:
        return f"{subject}: {malicious}/{total} vendors flag this as malicious"
    if suspicious:
        return f"{subject}: {suspicious}/{total} vendors flag this as suspicious"
    return f"{subject}: 0/{total} vendors flag this as malicious"


def _flagged_vendors(results: dict) -> list[str]:
    return [
        f"{name}: {info.get('result')}"
        for name, info in results.items()
        if info.get("category") in ("malicious", "suspicious")
    ]


def register_virustotal_tools(mcp: FastMCP):

    @mcp.tool()
    async def virustotal_lookup_ip(ip: str) -> dict:
        """Look up an IP address on VirusTotal (70+ AV/security vendor verdicts).
        Use this to check if an IP is known-malicious (C2, scanning, malware hosting).
        """
        api_key = _api_key()
        if not api_key:
            return {"ip": ip, "error": "VirusTotal API key not configured in Tracecat Credentials"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{VT_BASE_URL}/ip_addresses/{ip}",
                    headers={"x-apikey": api_key},
                )
                if r.status_code == 404:
                    return {"ip": ip, "known": False, "summary": f"{ip} not seen by VirusTotal"}
                r.raise_for_status()
                attrs = r.json().get("data", {}).get("attributes", {})
                stats = attrs.get("last_analysis_stats", {})
                return {
                    "ip": ip,
                    "known": True,
                    "reputation": attrs.get("reputation", 0),
                    "malicious_votes": stats.get("malicious", 0),
                    "suspicious_votes": stats.get("suspicious", 0),
                    "total_vendors": sum(stats.values()) if stats else 0,
                    "flagged_by": _flagged_vendors(attrs.get("last_analysis_results", {}))[:10],
                    "as_owner": attrs.get("as_owner", "Unknown"),
                    "country": attrs.get("country", "Unknown"),
                    "summary": _stats_summary(ip, stats),
                }
        except httpx.HTTPStatusError as e:
            return {"ip": ip, "error": f"VirusTotal HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"ip": ip, "error": str(e)}

    @mcp.tool()
    async def virustotal_lookup_domain(domain: str) -> dict:
        """Look up a domain on VirusTotal (70+ AV/security vendor verdicts).
        Use this to check if a domain is known-malicious (phishing, C2, malware distribution).
        """
        api_key = _api_key()
        if not api_key:
            return {"domain": domain, "error": "VirusTotal API key not configured in Tracecat Credentials"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{VT_BASE_URL}/domains/{domain}",
                    headers={"x-apikey": api_key},
                )
                if r.status_code == 404:
                    return {"domain": domain, "known": False, "summary": f"{domain} not seen by VirusTotal"}
                r.raise_for_status()
                attrs = r.json().get("data", {}).get("attributes", {})
                stats = attrs.get("last_analysis_stats", {})
                return {
                    "domain": domain,
                    "known": True,
                    "reputation": attrs.get("reputation", 0),
                    "malicious_votes": stats.get("malicious", 0),
                    "suspicious_votes": stats.get("suspicious", 0),
                    "total_vendors": sum(stats.values()) if stats else 0,
                    "flagged_by": _flagged_vendors(attrs.get("last_analysis_results", {}))[:10],
                    "categories": list(attrs.get("categories", {}).values())[:5],
                    "summary": _stats_summary(domain, stats),
                }
        except httpx.HTTPStatusError as e:
            return {"domain": domain, "error": f"VirusTotal HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"domain": domain, "error": str(e)}

    @mcp.tool()
    async def virustotal_lookup_hash(file_hash: str) -> dict:
        """Look up a file hash (MD5/SHA1/SHA256) on VirusTotal (70+ AV vendor verdicts).
        Use this to check if a file is known malware.
        """
        api_key = _api_key()
        if not api_key:
            return {"hash": file_hash, "error": "VirusTotal API key not configured in Tracecat Credentials"}
        return await lookup_hash(file_hash, api_key)


async def lookup_hash(file_hash: str, api_key: str) -> dict:
    """Core VirusTotal hash lookup, reusable outside the @mcp.tool wrapper
    above (e.g. by the hash_lookup_all_sources composite tool)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{VT_BASE_URL}/files/{file_hash}",
                headers={"x-apikey": api_key},
            )
            if r.status_code == 404:
                return {"hash": file_hash, "known": False, "summary": f"{file_hash} not seen by VirusTotal"}
            r.raise_for_status()
            attrs = r.json().get("data", {}).get("attributes", {})
            stats = attrs.get("last_analysis_stats", {})
            return {
                "hash": file_hash,
                "known": True,
                "malicious_votes": stats.get("malicious", 0),
                "suspicious_votes": stats.get("suspicious", 0),
                "total_vendors": sum(stats.values()) if stats else 0,
                "flagged_by": _flagged_vendors(attrs.get("last_analysis_results", {}))[:10],
                "type_description": attrs.get("type_description", "Unknown"),
                "meaningful_name": attrs.get("meaningful_name", "Unknown"),
                "summary": _stats_summary(file_hash, stats),
            }
    except httpx.HTTPStatusError as e:
        return {"hash": file_hash, "error": f"VirusTotal HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"hash": file_hash, "error": str(e)}
