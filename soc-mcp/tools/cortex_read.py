"""Cortex XDR read-only tools for SOC MCP server.

Deliberately separated from cortex_actions.py so this module can be exposed
as its own MCP integration (soc-mcp) and bound only to investigative agents
(e.g. investigator-agent), while the destructive/state-changing tools in
cortex_actions.py are bound only to responder-agent via a separate
soc-mcp-actions integration. Tracecat's agent presets bind whole MCP servers,
not individual tools within one, so this split is what actually enforces
least privilege — not just prompt instructions.
"""

import asyncio
import time

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

    @mcp.tool()
    async def cortex_get_cases(status: str = "New", limit: int = 20) -> dict:
        """List Cortex XDR cases (the "Cases & Issues" console view, not the
        legacy Incidents API). Optionally filtered by status_progress (New,
        In Progress, Resolved). Sorted newest-first by creation_time. Each
        case includes hosts/users, severity, MITRE tactics, and issue_ids
        (pass those to cortex_get_case_issues for the underlying issues).
        """
        url = f"{CORTEX_API_URL}/public_api/v1/case/search"
        request_data: dict = {
            "search_from": 0,
            "search_to": limit,
            "sort": {"field": "creation_time", "keyword": "desc"},
        }
        if status:
            request_data["filters"] = [
                {"field": "status_progress", "operator": "in", "value": [status]}
            ]
        payload = {"request_data": request_data}
        async with _client() as client:
            resp = await client.post(url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return resp.json()

    @mcp.tool()
    async def cortex_get_case_issues(issue_ids: list[int]) -> dict:
        """Get full detail for the issues belonging to a case. Pass the
        case's own issue_ids list (from cortex_get_cases). Issue-level
        severity/status values use different casing than case-level
        (e.g. "LOW"/"New" nested under status.progress) — do not assume
        they match the parent case's fields.
        """
        url = f"{CORTEX_API_URL}/public_api/v1/issue/search"
        payload = {
            "request_data": {
                "filters": [
                    {"field": "id", "operator": "in", "value": issue_ids}
                ]
            },
            "search_from": 0,
            "search_to": len(issue_ids) or 1,
            "include_fields": ["normalized_fields", "custom_fields"],
        }
        async with _client() as client:
            resp = await client.post(url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return resp.json()

    @mcp.tool()
    async def cortex_get_alerts_by_id(alert_ids: list[int]) -> dict:
        """Get full alert detail for a case's issues, using the legacy
        Alerts API (an issue's own "id" is the same value as its alert_id —
        confirmed against this tenant). Unlike cortex_get_case_issues, this
        returns populated host_name/host_ip/user_name plus the causality
        chain: causality_actor_process_image_name/command_line (the process
        that started the chain) and actor_process_image_name/command_line
        (the process that performed the action), which is the data behind
        Cortex's "Executions" case tab — there is no dedicated public API
        for that tab, but this endpoint carries the same underlying fields.
        """
        url = f"{CORTEX_API_URL}/public_api/v1/alerts/get_alerts"
        payload = {
            "request_data": {
                "filters": [
                    {"field": "alert_id_list", "operator": "in", "value": [int(a) for a in alert_ids]}
                ]
            }
        }
        async with _client() as client:
            resp = await client.post(url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        return resp.json()

    @mcp.tool()
    async def cortex_get_case_artifacts(case_id: int) -> dict:
        """Get the file and network artifacts (hashes, signatures, WildFire
        verdicts, remote IPs/domains) linked to a case. Requires the API
        key's role to include "Alerts and Incidents" view permission, not
        just "Cases and Issues" — a 403 here usually means that permission
        is missing, not a bad case_id.
        """
        url = f"{CORTEX_API_URL}/public_api/v1/case/artifacts/{case_id}/"
        async with _client() as client:
            resp = await client.get(url, headers=_headers())
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        data = resp.json()
        # This endpoint returns a top-level JSON array (one entry per
        # requested case_id), not the {"reply": {...}} envelope every other
        # Cortex endpoint uses. Unwrap it since MCP tools must return a dict.
        if isinstance(data, list):
            return data[0] if data else {}
        return data

    @mcp.tool()
    async def cortex_get_process_causality_chain(causality_id: str, lookback_days: int = 30) -> dict:
        """Reconstruct a process execution chain (Cortex's "Executions" case
        tab has no public API of its own) by running an XQL query against
        the raw xdr_data dataset, filtered by actor_process_causality_id.
        Returns the chronological list of events (injections, network
        connections, RPC calls, module loads) for that one process chain.
        Get the causality_id from an issue's raw telemetry first — there is
        no direct case/issue field for it in this API version.
        """
        now_ms = int(time.time() * 1000)
        from_ms = now_ms - lookback_days * 24 * 60 * 60 * 1000
        query = (
            "dataset = xdr_data "
            f'| filter actor_process_causality_id = "{causality_id}" '
            "| fields agent_hostname, event_timestamp, actor_process_image_name, "
            "actor_process_image_command_line, action_process_image_name, "
            "event_type, event_sub_type "
            "| sort asc event_timestamp "
            "| limit 50"
        )
        start_url = f"{CORTEX_API_URL}/public_api/v1/xql/start_xql_query/"
        start_payload = {
            "request_data": {
                "query": query,
                "tenants": [],
                "timeframe": {"from": from_ms, "to": now_ms},
            }
        }
        async with _client() as client:
            start_resp = await client.post(start_url, headers=_headers(), json=start_payload)
            if start_resp.status_code >= 400:
                return {"error": f"HTTP {start_resp.status_code}", "detail": start_resp.text}
            query_id = start_resp.json().get("reply")
            if not isinstance(query_id, str):
                return {"error": "xql_start_failed", "detail": start_resp.json()}

            results_url = f"{CORTEX_API_URL}/public_api/v1/xql/get_query_results/"
            results_payload = {
                "request_data": {"query_id": query_id, "pending_flag": False, "format": "json"}
            }
            for _ in range(15):
                await asyncio.sleep(2)
                results_resp = await client.post(results_url, headers=_headers(), json=results_payload)
                if results_resp.status_code >= 400:
                    return {"error": f"HTTP {results_resp.status_code}", "detail": results_resp.text}
                data = results_resp.json()
                status = data.get("reply", {}).get("status")
                if status in ("SUCCESS", "FAIL"):
                    return data
            return {"error": "xql_timeout", "detail": "Query did not finish within 30s"}
