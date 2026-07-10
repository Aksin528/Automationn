"""Splunk tools for SOC MCP server."""

import os
import re
import httpx


SPLUNK_BASE_URL = os.environ.get("SPLUNK_BASE_URL", "https://localhost:8089")
SPLUNK_TOKEN = os.environ.get("SPLUNK_TOKEN", "")
SPLUNK_VERIFY_SSL = os.environ.get("SPLUNK_VERIFY_SSL", "true").lower() == "true"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=SPLUNK_VERIFY_SSL, follow_redirects=True)

def _headers() -> dict:
    return {"Authorization": f"Bearer {SPLUNK_TOKEN}"}


def _normalize_earliest(value: str) -> str:
    """Normalize relative time strings to Splunk's "-N<unit>" format.

    Models frequently omit the leading "-" (e.g. "30m") or use Splunk's raw SPL
    syntax (e.g. "now-30m"). Both mean the same thing here, so fix them up
    instead of erroring. Absolute timestamps and "now" pass through unchanged.
    """
    if not value:
        return value
    v = value.strip()
    if v == "now" or v.startswith("-"):
        return v
    if v.startswith("now-"):
        return "-" + v[len("now-"):]
    if re.fullmatch(r"\d+[smhdwMy]", v):
        return "-" + v
    return v


# Fields kept in splunk_notable_events results. Notable events carry ~80 fields
# each, many of them huge (drilldown_searches, full mitre_description, _raw) —
# large enough that even a handful of events can blow past a small local model's
# context window. Keep only what a SOC analyst summary actually needs.
_NOTABLE_EVENT_ALLOWED_FIELDS = frozenset({
    "_time",
    "rule_title",
    "rule_name",
    "severity",
    "urgency",
    "status",
    "status_label",
    "status_group",
    "disposition",
    "disposition_label",
    "owner",
    "owner_realname",
    "event_id",
    "rule_id",
    "index",
    "sourcetype",
    "security_domain",
    "notable_type",
    "risk_score",
    "src_risk_score",
    "dest_risk_score",
    "user_risk_score",
    "annotations.mitre_attack.mitre_tactic",
    "annotations.mitre_attack.mitre_tactic_id",
    "annotations.mitre_attack.mitre_technique",
    "annotations.mitre_attack.mitre_technique_id",
    "dest",
    "src",
    "user",
    "host",
    "dvc",
})


# Fields that Splunk ES's Incident Review dashboard substitutes as $token$
# placeholders inside rule_title. The dashboard resolves these client-side;
# raw search results (what this API returns) keep the literal "$dest$" text,
# so we resolve them here using the event's own field values.
_RULE_TITLE_TOKEN_FIELDS = ("dest", "src", "user", "dvc", "process_name", "host")


def _resolve_rule_title(event: dict) -> str | None:
    """Replace $field$ placeholders in rule_title with the event's real values."""
    rule_title = event.get("rule_title")
    if not isinstance(rule_title, str) or "$" not in rule_title:
        return rule_title
    for field in _RULE_TITLE_TOKEN_FIELDS:
        token = f"${field}$"
        if token in rule_title:
            value = event.get(field)
            if isinstance(value, str) and value:
                rule_title = rule_title.replace(token, value)
    return rule_title


def _slim_notable_event(event: dict) -> dict:
    """Drop heavy/unused fields from a notable event, keeping the analyst-facing ones."""
    return {k: v for k, v in event.items() if k in _NOTABLE_EVENT_ALLOWED_FIELDS}


def _resolve_notable_response(data: dict) -> dict:
    """Resolve rule_title placeholders for every result row using the full raw event.

    Must run before _slim_notable_event, since some substitution fields (e.g.
    process_name) aren't in the analyst-facing allowed field set.
    """
    results = data.get("results")
    if isinstance(results, list):
        data = dict(data)
        resolved = []
        for ev in results:
            if isinstance(ev, dict):
                ev = dict(ev)
                ev["rule_title"] = _resolve_rule_title(ev)
            resolved.append(ev)
        data["results"] = resolved
    return data


def _slim_notable_response(data: dict) -> dict:
    """Apply _slim_notable_event to every result row in a Splunk search response."""
    results = data.get("results")
    if isinstance(results, list):
        data = dict(data)
        data["results"] = [
            _slim_notable_event(ev) if isinstance(ev, dict) else ev for ev in results
        ]
    return data


def _handle_response(resp: httpx.Response) -> dict:
    """Raise with Splunk's own diagnostic message attached, instead of a generic httpx error."""
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        try:
            detail = resp.json().get("messages", resp.text)
        except Exception:
            detail = resp.text
        raise httpx.HTTPStatusError(
            f"{e}\nSplunk response: {detail}", request=e.request, response=e.response
        ) from None
    return resp.json()


# Full Splunk ES "Incident Review - Main" enrichment pipeline (from savedsearches.conf),
# with the dashboard's $token$ filter placeholders removed since they're blank by default.
# Requires the SplunkEnterpriseSecuritySuite app namespace to resolve these macros.
_ES_NOTABLE_ENRICHMENT = (
    "| eval `get_event_id_meval`,rule_id=event_id "
    "| dedup rule_id "
    "| fields - host_* "
    "| tags outputfield=tag "
    "| `mvappend_field(tag,orig_tag)` "
    "| `notable_xref_lookup` "
    "| `get_correlations_performant` "
    "| `get_current_status` "
    "| `get_owner` "
    "| `get_urgency` "
    "| typer "
    "| tags outputfield=tag "
    "| `mvappend_field(tag,orig_tag)` "
    "| `suppression_extract` "
    "| search NOT suppression=* "
    "| `risk_correlation` "
    "| `get_mitre_annotations` "
    "| `get_notable_type` "
    "| `get_orig_source` "
    "| `add_normalized_risk_object`"
)

ES_APP_SEARCH_URL_TMPL = "{base}/servicesNS/nobody/SplunkEnterpriseSecuritySuite/search/jobs"


def register_splunk_tools(mcp):

    @mcp.tool()
    async def splunk_search(
        query: str,
        earliest: str = "-1h",
        latest: str = "now",
        limit: int = 100,
    ) -> dict:
        """Run a SPL search query on Splunk. Query must start with 'search'.
        Example: search index=main EventCode=4625 | head 50"""
        if not query.strip().startswith("search "):
            query = "search " + query
        earliest = _normalize_earliest(earliest)
        async with _client() as client:
            resp = await client.post(
                f"{SPLUNK_BASE_URL}/services/search/jobs",
                headers=_headers(),
                data={
                    "search": query,
                    "earliest_time": earliest,
                    "latest_time": latest,
                    "exec_mode": "oneshot",
                    "output_mode": "json",
                    "max_count": limit,
                    "count": 0,
                },
            )
            return _handle_response(resp)

    @mcp.tool()
    async def splunk_notable_events(
        severity: str = "all",
        earliest: str = "-24h",
        limit: int = 10,
        full: bool = False,
    ) -> dict:
        """Get Splunk ES Incident Review notable events with enriched data
        (owner, status, disposition, MITRE ATT&CK, risk scores). By default the
        response fields are trimmed to the analyst-relevant set to keep results
        compact and avoid overflowing small models' context windows. Pass
        full=true to get every field (drilldown_searches, full descriptions,
        _raw, etc.) — used by automation/workflows that build case descriptions,
        not by chat agents.
        Severity options: informational, low, medium, high, critical, all (default: all)"""
        earliest = _normalize_earliest(earliest)
        query = f"search `get_notable_index` earliest={earliest} {_ES_NOTABLE_ENRICHMENT}"
        if severity != "all":
            query += f" | search severity={severity}"
        query += f" | head {limit}"
        async with _client() as client:
            resp = await client.post(
                ES_APP_SEARCH_URL_TMPL.format(base=SPLUNK_BASE_URL),
                headers=_headers(),
                data={
                    "search": query,
                    "exec_mode": "oneshot",
                    "output_mode": "json",
                    "count": 0,
                },
            )
            data = _handle_response(resp)
            data = _resolve_notable_response(data)
            return data if full else _slim_notable_response(data)

    @mcp.tool()
    async def splunk_list_indexes() -> dict:
        """List all available Splunk indexes."""
        async with _client() as client:
            resp = await client.get(
                f"{SPLUNK_BASE_URL}/services/data/indexes",
                headers=_headers(),
                params={"output_mode": "json", "count": 0},
            )
            return _handle_response(resp)

    @mcp.tool()
    async def splunk_list_sourcetypes(index: str = "main") -> dict:
        """List sourcetypes available in a Splunk index."""
        query = f"search index={index} | stats count by sourcetype | sort -count"
        async with _client() as client:
            resp = await client.post(
                f"{SPLUNK_BASE_URL}/services/search/jobs",
                headers=_headers(),
                data={
                    "search": query,
                    "exec_mode": "oneshot",
                    "output_mode": "json",
                    "count": 0,
                },
            )
            return _handle_response(resp)

    @mcp.tool()
    async def splunk_search_by_ip(
        ip: str,
        earliest: str = "-24h",
        limit: int = 100,
    ) -> dict:
        """Search all Splunk events related to a specific IP address."""
        earliest = _normalize_earliest(earliest)
        query = f"search (src_ip={ip} OR dest_ip={ip} OR src={ip} OR dest={ip}) earliest={earliest} | head {limit}"
        async with _client() as client:
            resp = await client.post(
                f"{SPLUNK_BASE_URL}/services/search/jobs",
                headers=_headers(),
                data={
                    "search": query,
                    "exec_mode": "oneshot",
                    "output_mode": "json",
                    "count": 0,
                },
            )
            return _handle_response(resp)

    @mcp.tool()
    async def splunk_search_by_user(
        username: str,
        earliest: str = "-24h",
        limit: int = 100,
    ) -> dict:
        """Search all Splunk events related to a specific username."""
        earliest = _normalize_earliest(earliest)
        query = f"search (user={username} OR src_user={username} OR User={username}) earliest={earliest} | head {limit}"
        async with _client() as client:
            resp = await client.post(
                f"{SPLUNK_BASE_URL}/services/search/jobs",
                headers=_headers(),
                data={
                    "search": query,
                    "exec_mode": "oneshot",
                    "output_mode": "json",
                    "count": 0,
                },
            )
            return _handle_response(resp)
