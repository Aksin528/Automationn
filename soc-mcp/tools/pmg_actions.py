"""Proxmox Mail Gateway (PMG) write/response actions for SOC MCP Actions
server.

Adds a sender email or domain to PMG's built-in global "Blacklist" who-group
(confirmed on this tenant to be ogroup id 2, but looked up by name each call
instead of hardcoding the id, since it is environment-specific). Once added,
PMG's existing "Blacklist" rule (already wired to a Block action on this
tenant) blocks all future mail from that sender/domain — no new Rule/Action
objects need to be created, matching this tenant's confirmed setup.

Write call (POST) requires both the PMGAuthCookie cookie AND the
CSRFPreventionToken header, unlike the read-only GET calls in pmg_read.py.
"""

from .pmg_common import PMG_URL, _client, _login_full


def register_pmg_action_tools(mcp):

    @mcp.tool()
    async def pmg_add_to_blacklist(scope: str, value: str) -> dict:
        """Add a sender email address or domain to Proxmox Mail Gateway's
        global Blacklist. All future mail from that sender/domain will be
        blocked automatically by PMG's existing Blacklist rule.

        scope: "email" to block a single sender address, or "domain" to
        block every address at that domain.
        value: the email address (for scope="email") or bare domain (for
        scope="domain"), e.g. "attacker@bad.com" or "bad.com".

        Only call this after explicit human approval — this is a real,
        state-changing, org-wide action on the live mail gateway.
        """
        if scope not in ("email", "domain"):
            return {"error": f"invalid scope {scope!r}, must be 'email' or 'domain'"}

        async with _client() as client:
            ticket, csrf = await _login_full(client)
            cookies = {"PMGAuthCookie": ticket}
            headers = {"CSRFPreventionToken": csrf}

            who_resp = await client.get(
                f"{PMG_URL}/api2/json/config/ruledb/who", cookies=cookies
            )
            if who_resp.status_code >= 400:
                return {"error": f"HTTP {who_resp.status_code}", "detail": who_resp.text}
            groups = who_resp.json().get("data") or []
            blacklist_group = next((g for g in groups if g.get("name") == "Blacklist"), None)
            if not blacklist_group:
                return {"error": "No 'Blacklist' who-group found in PMG ruledb"}
            ogroup = blacklist_group["id"]

            add_resp = await client.post(
                f"{PMG_URL}/api2/json/config/ruledb/who/{ogroup}/{scope}",
                cookies=cookies,
                headers=headers,
                data={scope: value},
            )
            if add_resp.status_code >= 400:
                return {"error": f"HTTP {add_resp.status_code}", "detail": add_resp.text}

        return {"blocked": True, "scope": scope, "value": value, "ogroup": ogroup}
