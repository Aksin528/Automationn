"""abuse.ch threat intelligence tools for SOC MCP server -- covers
ThreatFox (general IOCs), URLhaus (malware-distribution URLs/hosts), and
MalwareBazaar (malware sample hashes).

Auth: all three abuse.ch platforms share one Auth-Key, read from the
incoming MCP request's `X-AbuseCH-Key` HTTP header (see virustotal_read.py
for why -- resolved by Tracecat from its Credentials store per call, not
an env var).
"""

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

THREATFOX_URL = "https://threatfox-api.abuse.ch/api/v1/"
URLHAUS_URL = "https://urlhaus-api.abuse.ch/v1"
MALWAREBAZAAR_URL = "https://mb-api.abuse.ch/api/v1/"


def _api_key() -> str | None:
    return get_http_headers().get("x-abusech-key")


def register_abusech_tools(mcp: FastMCP):

    @mcp.tool()
    async def threatfox_lookup_ioc(ioc: str) -> dict:
        """Look up an IOC (IP, domain, hash, or URL) on abuse.ch ThreatFox.
        Returns known malware family and threat type if this IOC is a tracked
        indicator of compromise. Use this for general-purpose IOC lookups.
        """
        api_key = _api_key()
        if not api_key:
            return {"ioc": ioc, "error": "abuse.ch API key not configured in Tracecat Credentials"}
        return await lookup_ioc_threatfox(ioc, api_key)

    @mcp.tool()
    async def urlhaus_lookup_url(url: str) -> dict:
        """Look up a URL on abuse.ch URLhaus (known malware-distribution URLs).
        Use this to check if a specific URL is a known malware download link.
        """
        api_key = _api_key()
        if not api_key:
            return {"url": url, "error": "abuse.ch API key not configured in Tracecat Credentials"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    f"{URLHAUS_URL}/url/",
                    headers={"Auth-Key": api_key},
                    data={"url": url},
                )
                r.raise_for_status()
                body = r.json()
                if body.get("query_status") != "ok":
                    return {"url": url, "known": False, "summary": f"{url}: not found in URLhaus"}
                return {
                    "url": url,
                    "known": True,
                    "url_status": body.get("url_status", "unknown"),
                    "threat": body.get("threat", "Unknown"),
                    "tags": (body.get("tags") or [])[:5],
                    "date_added": body.get("date_added"),
                    "summary": f"{url}: URLhaus threat={body.get('threat', 'unknown')}, status={body.get('url_status', 'unknown')}",
                }
        except httpx.HTTPStatusError as e:
            return {"url": url, "error": f"URLhaus HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"url": url, "error": str(e)}

    @mcp.tool()
    async def urlhaus_lookup_host(host: str) -> dict:
        """Look up a host/domain on abuse.ch URLhaus (known malware-distribution hosts).
        Use this to check if a domain has hosted malware-distribution URLs.
        """
        api_key = _api_key()
        if not api_key:
            return {"host": host, "error": "abuse.ch API key not configured in Tracecat Credentials"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    f"{URLHAUS_URL}/host/",
                    headers={"Auth-Key": api_key},
                    data={"host": host},
                )
                r.raise_for_status()
                body = r.json()
                if body.get("query_status") != "ok":
                    return {"host": host, "known": False, "summary": f"{host}: not found in URLhaus"}
                urls = body.get("urls") or []
                return {
                    "host": host,
                    "known": True,
                    "url_count": body.get("url_count", len(urls)),
                    "recent_threats": sorted({u.get("threat") for u in urls if u.get("threat")})[:5],
                    "first_seen": body.get("firstseen"),
                    "summary": f"{host}: {body.get('url_count', len(urls))} malware URL(s) hosted historically",
                }
        except httpx.HTTPStatusError as e:
            return {"host": host, "error": f"URLhaus HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"host": host, "error": str(e)}

    @mcp.tool()
    async def malwarebazaar_lookup_hash(file_hash: str) -> dict:
        """Look up a file hash (MD5/SHA1/SHA256) on abuse.ch MalwareBazaar.
        Use this to check if a file is a known-catalogued malware sample.
        """
        api_key = _api_key()
        if not api_key:
            return {"hash": file_hash, "error": "abuse.ch API key not configured in Tracecat Credentials"}
        return await lookup_hash_malwarebazaar(file_hash, api_key)


async def lookup_ioc_threatfox(ioc: str, api_key: str) -> dict:
    """Core ThreatFox lookup, reusable outside the @mcp.tool wrapper above
    (e.g. by the hash_lookup_all_sources composite tool)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                THREATFOX_URL,
                headers={"Auth-Key": api_key},
                json={"query": "search_ioc", "search_term": ioc},
            )
            r.raise_for_status()
            body = r.json()
            if body.get("query_status") != "ok":
                return {"ioc": ioc, "known": False, "summary": f"{ioc}: not found in ThreatFox"}
            entries = body.get("data") or []
            malware = sorted({e.get("malware_printable") for e in entries if e.get("malware_printable")})
            threat_types = sorted({e.get("threat_type_desc") for e in entries if e.get("threat_type_desc")})
            return {
                "ioc": ioc,
                "known": True,
                "match_count": len(entries),
                "malware_families": malware[:5],
                "threat_types": threat_types[:5],
                "confidence_level": max((e.get("confidence_level", 0) for e in entries), default=0),
                "summary": f"{ioc}: {len(entries)} ThreatFox match(es), malware: {', '.join(malware[:3]) or 'unknown'}",
            }
    except httpx.HTTPStatusError as e:
        return {"ioc": ioc, "error": f"ThreatFox HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"ioc": ioc, "error": str(e)}


async def lookup_hash_malwarebazaar(file_hash: str, api_key: str) -> dict:
    """Core MalwareBazaar hash lookup, reusable outside the @mcp.tool
    wrapper above (e.g. by the hash_lookup_all_sources composite tool)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                MALWAREBAZAAR_URL,
                headers={"Auth-Key": api_key},
                data={"query": "get_info", "hash": file_hash},
            )
            r.raise_for_status()
            body = r.json()
            entries = body.get("data") or []
            if body.get("query_status") != "ok" or not entries:
                return {"hash": file_hash, "known": False, "summary": f"{file_hash}: not found in MalwareBazaar"}
            entry = entries[0]
            return {
                "hash": file_hash,
                "known": True,
                "file_name": entry.get("file_name", "Unknown"),
                "file_type": entry.get("file_type", "Unknown"),
                "signature": entry.get("signature", "Unknown"),
                "first_seen": entry.get("first_seen"),
                "tags": (entry.get("tags") or [])[:5],
                "summary": f"{file_hash}: known malware ({entry.get('signature') or 'unclassified'})",
            }
    except httpx.HTTPStatusError as e:
        return {"hash": file_hash, "error": f"MalwareBazaar HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"hash": file_hash, "error": str(e)}
