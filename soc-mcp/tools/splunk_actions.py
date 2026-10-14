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

import json

import httpx

from .splunk import SPLUNK_BASE_URL, _client, _headers

# The review state of every notable (status, owner, urgency, disposition,
# last comment, who changed it and when) lives in this ES KV store
# collection; the lookup "incident_review_lookup" is a view of it. "time" is
# the epoch seconds of the last review change and can be filtered server side
# (verified against this tenant with a read-only probe).
_REVIEW_KV_URL_TMPL = (
    "{base}/servicesNS/nobody/SA-ThreatIntelligence"
    "/storage/collections/data/incident_review"
)
_MAX_FETCH = 20000  # the KV store's own default row cap is 50000
# httpx's default is 5 s; notable_update and the big KV read are slower than
# that on this tenant, which surfaced as "Upstream request timed out".
_HTTP_TIMEOUT = 60.0


def _kv_rows(resp):
    """The JSON list the KV store returned, or None when the call failed."""
    if resp.status_code >= 400:
        return None
    try:
        body = resp.json()
    except ValueError:
        return None
    return body if isinstance(body, list) else None


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value) -> str:
    return "" if value is None else str(value)


def _normalize_disposition(value: str) -> str:
    """Accept "7" or "disposition:7"; ES uses the "disposition:<n>" form."""
    value = value.strip()
    return f"disposition:{value}" if value.isdigit() else value


def register_splunk_action_tools(mcp):
    @mcp.tool()
    async def splunk_update_notable_event(
        event_id: str,
        status: str = "",
        comment: str = "",
        urgency: str = "",
        new_owner: str = "",
        disposition: str = "",
    ) -> dict:
        """Update a Splunk Enterprise Security notable event: status,
        comment, urgency, owner, and/or disposition. Only the fields you
        pass are changed -- at least one of
        status/comment/urgency/new_owner/disposition is required.

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

        new_owner: a Splunk user name (not an e-mail); "unassigned" clears
        the owner.

        disposition: a disposition ID, "disposition:<n>" (a bare number is
        accepted and expanded). Also tenant-specific, see reviewstatuses.conf.

        comment: ES can be configured to require a comment (minimum length)
        on every review change -- this tenant's log_review settings do.
        Always pass one together with status/owner/urgency/disposition.

        Only call this after explicit human approval -- this writes to a
        live Splunk ES notable event.
        """
        if not any([status, comment, urgency, new_owner, disposition]):
            return {
                "error": "At least one of status/comment/urgency/new_owner/"
                "disposition is required"
            }
        if status and not status.strip().isdigit():
            return {
                "error": 'status must be a numeric status ID such as "2", not a label'
            }

        url = f"{SPLUNK_BASE_URL}/services/notable_update"
        data: dict = {"ruleUIDs": event_id}
        if status:
            data["status"] = status.strip()
        if comment:
            data["comment"] = comment
        if urgency:
            data["urgency"] = urgency
        if new_owner:
            data["newOwner"] = new_owner
        if disposition:
            data["disposition"] = _normalize_disposition(disposition)

        try:
            async with _client() as client:
                resp = await client.post(
                    url,
                    headers=_headers(),
                    data=data,
                    params={"output_mode": "json"},
                    timeout=_HTTP_TIMEOUT,
                )
        except httpx.TimeoutException:
            # Splunk may still finish the update after we stopped waiting.
            return {
                "error": "timeout",
                "outcome_unknown": True,
                "detail": f"Splunk did not answer within {int(_HTTP_TIMEOUT)} s; "
                "the update may or may not have been applied",
            }
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text}
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text[:500]}
        # The body carries a success flag; do not report a refused change as
        # done (the sync workflows write their snapshot only after a success).
        if isinstance(body, dict) and body.get("success") is False:
            return {"error": "notable_update reported success=false", "detail": body}
        return {"status": "update_requested", "event_id": event_id, "response": body}

    @mcp.tool()
    async def splunk_list_modified_notables(
        since_epoch: float, limit: int = 200, offset: int = 0
    ) -> dict:
        """List notable review records changed at or after since_epoch
        (epoch SECONDS), oldest change first. Read-only; it lives here, next
        to splunk_update_notable_event, because the Splunk <-> Tracecat sync
        workflow already talks to this MCP server without auth headers. Not
        meant for agents.

        Each item: event_id (the notable's ruleUID, "<uuid>@@notable@@<hash>",
        the value splunk_update_notable_event takes), time (epoch seconds of
        the last review change), status (numeric ID as text), owner (Splunk
        user name, not an e-mail), urgency, disposition ("disposition:<n>"),
        comment (the LATEST review comment only) and user (who changed it).

        Only notables that were reviewed at least once have a record. Items
        are ordered by (time, event_id), so "offset" pages are stable even
        when many records share the same time (bulk edits). The reply has
        "truncated": true when more items than offset + limit matched; ask
        again with the same since_epoch and offset += limit.
        """
        try:
            since = float(since_epoch)
        except (TypeError, ValueError):
            return {"error": "since_epoch must be a number (epoch seconds)"}
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))

        url = _REVIEW_KV_URL_TMPL.format(base=SPLUNK_BASE_URL)
        params = {
            "output_mode": "json",
            "query": json.dumps({"time": {"$gte": since}}),
            "limit": _MAX_FETCH,
        }
        try:
            async with _client() as client:
                resp = await client.get(
                    url, headers=_headers(), params=params, timeout=_HTTP_TIMEOUT
                )
        except httpx.TimeoutException:
            return {
                "error": "timeout",
                "detail": f"Splunk did not answer within {int(_HTTP_TIMEOUT)} s",
            }
        rows = _kv_rows(resp)
        if rows is None:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text[:500]}
        if len(rows) >= _MAX_FETCH:
            return {
                "error": f"{_MAX_FETCH} or more records changed since "
                "since_epoch; use a later since_epoch"
            }

        newest: dict = {}
        for row in rows:
            event_id = _text(row.get("rule_id"))
            changed = _num(row.get("time"))
            if not event_id or changed is None:
                continue
            if event_id in newest and newest[event_id]["time"] >= changed:
                continue
            newest[event_id] = {
                "event_id": event_id,
                "time": changed,
                "status": _text(row.get("status")),
                "owner": _text(row.get("owner")),
                "urgency": _text(row.get("urgency")),
                "disposition": _text(row.get("disposition")),
                "comment": _text(row.get("comment")),
                "user": _text(row.get("user")),
            }
        ordered = sorted(newest.values(), key=lambda n: (n["time"], n["event_id"]))
        page = ordered[offset : offset + limit]
        return {
            "count": len(page),
            "total": len(ordered),
            "truncated": len(ordered) > offset + limit,
            "notables": page,
        }
