"""Proxmox Mail Gateway (PMG) mail tracking read-only tools for SOC MCP
server.

The tracker list endpoint (/nodes/{node}/tracker) does not support filtering
by dstatus server-side, so this tool fetches the list for the requested time
window and filters client-side, then fetches the per-message detail
(/nodes/{node}/tracker/{id}) for each match to get the full syslog line
history ("logs"), from which the SpamAssassin score, triggered rule name,
and (for quarantined mail) the quarantine ID are extracted via regex.

Confirmed dstatus meanings against this tenant's real tracker data:
- "B" = Blocked (content-filter rule matched, e.g. Blacklist)
- "Q" = Quarantined (moved to spam quarantine)
- "N" = Rejected (NOQUEUE, rejected at SMTP/RCPT stage, before content
  filtering — e.g. relay access denied)
- "A" = Accepted/delivered normally (not a security event)
- numeric codes (e.g. "2", "4") = outbound relay-leg DSN status classes,
  unrelated to content filtering — not treated as blocked mail here.
"""

import re
from datetime import UTC, datetime, timedelta

from .pmg_common import PMG_URL, _client, _get_node, _login


BLOCKED_DSTATUS = {"B", "Q", "N"}

_SA_SCORE_RE = re.compile(r"SA score=([\d.]+)/([\d.]+)")
_RULE_RE = re.compile(r"\(rule: ([^)]+)\)")
_QUARANTINE_ID_RE = re.compile(r"to spam quarantine - (\S+)")
_REJECT_REASON_RE = re.compile(r"NOQUEUE: reject: (.+?)(?:;|$)")


def _extract_detail(logs: list[str]) -> dict:
    sa_score = None
    sa_max = None
    rule = None
    quarantine_id = None
    reject_reason = None

    for line in logs:
        if sa_score is None:
            m = _SA_SCORE_RE.search(line)
            if m:
                sa_score, sa_max = m.group(1), m.group(2)
        if rule is None:
            m = _RULE_RE.search(line)
            if m:
                rule = m.group(1)
        if quarantine_id is None:
            m = _QUARANTINE_ID_RE.search(line)
            if m:
                quarantine_id = m.group(1)
        if reject_reason is None:
            m = _REJECT_REASON_RE.search(line)
            if m:
                reject_reason = m.group(1)

    return {
        "sa_score": sa_score,
        "sa_max": sa_max,
        "rule": rule,
        "quarantine_id": quarantine_id,
        "reject_reason": reject_reason,
    }


def register_pmg_read_tools(mcp):

    @mcp.tool()
    async def pmg_get_blocked_mail(
        hours_back: int = 24,
        max_results: int = 100,
    ) -> dict:
        """Query Proxmox Mail Gateway's mail tracker for blocked, quarantined,
        and rejected mail in the given time window (dstatus in B/Q/N).

        Each returned item has the tracker metadata (from/to/time/size/qid)
        plus a "detail" object with sa_score/sa_max (SpamAssassin score),
        rule (triggered content-filter rule name), quarantine_id (for
        dstatus="Q", the quarantine reference), and reject_reason (for
        dstatus="N", the SMTP-level rejection message).

        max_results caps the number of matched (not total) items whose
        detail is fetched, to bound the number of extra API calls.
        """
        now = datetime.now(UTC)
        start = now - timedelta(hours=hours_back)
        starttime = int(start.timestamp())

        async with _client() as client:
            ticket = await _login(client)
            node = await _get_node(client, ticket)
            cookies = {"PMGAuthCookie": ticket}

            list_resp = await client.get(
                f"{PMG_URL}/api2/json/nodes/{node}/tracker",
                params={"starttime": starttime},
                cookies=cookies,
            )
            if list_resp.status_code >= 400:
                return {"error": f"HTTP {list_resp.status_code}", "detail": list_resp.text}

            items = list_resp.json().get("data") or []
            matched = [i for i in items if i.get("dstatus") in BLOCKED_DSTATUS][:max_results]

            results = []
            for item in matched:
                mail_id = item.get("id")
                detail_resp = await client.get(
                    f"{PMG_URL}/api2/json/nodes/{node}/tracker/{mail_id}",
                    cookies=cookies,
                )
                logs = []
                if detail_resp.status_code < 400:
                    logs = detail_resp.json().get("data", {}).get("logs") or []

                results.append({
                    **item,
                    "detail": _extract_detail(logs),
                    "logs": logs,
                })

        return {"total-in-window": len(items), "matched-count": len(results), "mails": results}
