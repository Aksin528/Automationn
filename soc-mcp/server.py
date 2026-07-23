"""SOC MCP Server — Splunk, Wazuh, Cortex XDR, Trellix DLP, Check Point Mail Security read-only tools for AI agents.

Investigative/read-only tools only. Destructive Cortex response actions
(isolate, quarantine, blocklist, etc.) live in server_actions.py as a
separate MCP server, so they can be bound to responder-agent only — Tracecat
binds whole MCP integrations to an agent preset, not individual tools within
one, so this split is what enforces least privilege for investigator-agent
and other read-only agents.
"""

from fastmcp import FastMCP
from tools.splunk import register_splunk_tools
from tools.ip_geolocation import register_ip_geolocation_tools
from tools.cortex_read import register_cortex_read_tools
from tools.trellix_read import register_trellix_read_tools
from tools.checkpoint_read import register_checkpoint_read_tools

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
# register_wazuh_tools(mcp)

if __name__ == "__main__":
    import os
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8100"))
    mcp.run(transport="http", host=host, port=port)
