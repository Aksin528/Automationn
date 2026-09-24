"""SOC MCP Server — Splunk, Wazuh, Cortex XDR, Trellix DLP, Check Point Mail
Security, Proxmox Mail Gateway read-only tools, and threat-intel lookups
(VirusTotal, AbuseIPDB, AlienVault OTX, abuse.ch) for AI agents.

Investigative/read-only tools only. Destructive Cortex response actions
(isolate, quarantine, blocklist, etc.) live in server_actions.py as a
separate MCP server, so they can be bound to responder-agent only — Tracecat
binds whole MCP integrations to an agent preset, not individual tools within
one, so this split is what enforces least privilege for investigator-agent
and other read-only agents.

The four threat-intel modules (virustotal_read, abuseipdb_read, otx_read,
abusech_read) don't read their API keys from env vars like the other tool
modules -- they pull them per-call from incoming MCP request headers
(X-VirusTotal-Key, X-AbuseIPDB-Key, X-OTX-Key, X-AbuseCH-Key), which
Tracecat resolves from its own Credentials store via the soc-mcp MCP
integration's "Custom" auth type. See virustotal_read.py for details.
"""

from fastmcp import FastMCP
from tools.abusech_read import register_abusech_tools
from tools.abuseipdb_read import register_abuseipdb_tools
from tools.checkpoint_read import register_checkpoint_read_tools
from tools.cortex_read import register_cortex_read_tools
from tools.ip_geolocation import register_ip_geolocation_tools
from tools.otx_read import register_otx_tools
from tools.pmg_read import register_pmg_read_tools
from tools.splunk import register_splunk_tools
from tools.threat_intel_composite import register_threat_intel_composite_tools
from tools.trellix_read import register_trellix_read_tools
from tools.virustotal_read import register_virustotal_tools

# Gelecekde elave edilecek:
# from tools.wazuh import register_wazuh_tools

mcp = FastMCP(
    name="soc-mcp",
    instructions="SOC analyst MCP server. Use these tools to investigate security alerts, search logs, and triage incidents.",
)

register_splunk_tools(mcp)
register_ip_geolocation_tools(mcp)
register_cortex_read_tools(mcp)
register_trellix_read_tools(mcp)
register_checkpoint_read_tools(mcp)
register_pmg_read_tools(mcp)
register_virustotal_tools(mcp)
register_abuseipdb_tools(mcp)
register_otx_tools(mcp)
register_abusech_tools(mcp)
register_threat_intel_composite_tools(mcp)
# register_wazuh_tools(mcp)

if __name__ == "__main__":
    import os
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8100"))
    mcp.run(transport="http", host=host, port=port)
