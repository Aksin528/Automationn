"""SOC MCP Actions Server — destructive/state-changing Cortex XDR response
actions (isolate, quarantine, blocklist, etc.) for AI agents.

Kept as a separate MCP server (separate FastMCP instance, separate port) from
server.py's read-only tools so it can be registered as its own Tracecat MCP
integration and bound only to responder-agent. See tools/cortex_read.py's
module docstring for the full rationale.
"""

from fastmcp import FastMCP
from tools.cortex_actions import register_cortex_action_tools

mcp = FastMCP(
    name="soc-mcp-actions",
    instructions="SOC response-action MCP server. Executes real, state-changing containment actions (isolate, quarantine, blocklist) on Cortex XDR. Only call these after explicit human approval.",
)

register_cortex_action_tools(mcp)

if __name__ == "__main__":
    import os
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_ACTIONS_PORT", "8101"))
    mcp.run(transport="http", host=host, port=port)
