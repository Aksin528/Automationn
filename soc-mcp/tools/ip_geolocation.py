"""IP Geolocation tools for SOC MCP server."""

import httpx
from fastmcp import FastMCP


def register_ip_geolocation_tools(mcp: FastMCP):

    @mcp.tool()
    async def geolocate_ip(ip: str) -> dict:
        """Get geolocation information for a single IP address.
        Returns country, city, ISP, organization, and threat context.
        Use this when you need to know where an IP address is located.
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"http://ip-api.com/json/{ip}",
                    params={"fields": "status,country,regionName,city,isp,org,as,query"}
                )
                data = r.json()
                if data.get("status") == "success":
                    return {
                        "ip": ip,
                        "country": data.get("country", "Unknown"),
                        "region": data.get("regionName", "Unknown"),
                        "city": data.get("city", "Unknown"),
                        "isp": data.get("isp", "Unknown"),
                        "org": data.get("org", "Unknown"),
                        "as": data.get("as", "Unknown"),
                        "summary": f"{ip} → {data.get('country','?')}, {data.get('city','?')} ({data.get('isp','?')})"
                    }
                return {"ip": ip, "error": "Lookup failed", "summary": ip}
        except Exception as e:
            return {"ip": ip, "error": str(e), "summary": ip}

    @mcp.tool()
    async def geolocate_batch(ips: list[str]) -> list[dict]:
        """Get geolocation information for multiple IP addresses (max 10).
        Use this when you need to geolocate several IPs at once.
        """
        results = []
        for ip in ips[:10]:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.get(
                        f"http://ip-api.com/json/{ip}",
                        params={"fields": "status,country,regionName,city,isp,org,as,query"}
                    )
                    data = r.json()
                    if data.get("status") == "success":
                        results.append({
                            "ip": ip,
                            "country": data.get("country", "Unknown"),
                            "region": data.get("regionName", "Unknown"),
                            "city": data.get("city", "Unknown"),
                            "isp": data.get("isp", "Unknown"),
                            "org": data.get("org", "Unknown"),
                            "summary": f"{ip} → {data.get('country','?')}, {data.get('city','?')} ({data.get('isp','?')})"
                        })
                    else:
                        results.append({"ip": ip, "error": "Lookup failed", "summary": ip})
            except Exception as e:
                results.append({"ip": ip, "error": str(e), "summary": ip})
        return results
