"""
MCP (Model Context Protocol) client for Elwin.
Connects to configured MCP servers, exposes their tools to the agent.

Configure servers in mcp_servers.json at the repo root:
{
  "filesystem": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/michael/Repo"]
  },
  "github": {
    "type": "sse",
    "url": "http://localhost:8090/sse"
  }
}

Each server's tools appear in the agent as "{server}__{tool_name}".
"""

import asyncio
import json
import logging
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

from .tools import ToolDef, register_tool

logger = logging.getLogger(__name__)

_SERVERS_PATH = Path(__file__).parent.parent / "mcp_servers.json"
_loaded = False


def _server_configs() -> dict[str, dict]:
    if not _SERVERS_PATH.exists():
        return {}
    try:
        return json.loads(_SERVERS_PATH.read_text())
    except Exception as e:
        logger.warning("mcp_servers.json unreadable: %s", e)
        return {}


# ── Async helpers ─────────────────────────────────────────────

async def _list_stdio(name: str, cfg: dict) -> list[dict]:
    params = StdioServerParameters(
        command=cfg["command"],
        args=cfg.get("args", []),
        env=cfg.get("env") or None,
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.list_tools()
            return [{"server": name, "name": t.name, "description": t.description or "",
                     "inputSchema": t.inputSchema} for t in result.tools]


async def _list_sse(name: str, cfg: dict) -> list[dict]:
    async with sse_client(cfg["url"], headers=cfg.get("headers", {})) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.list_tools()
            return [{"server": name, "name": t.name, "description": t.description or "",
                     "inputSchema": t.inputSchema} for t in result.tools]


async def _call_stdio(cfg: dict, tool_name: str, arguments: dict) -> str:
    params = StdioServerParameters(
        command=cfg["command"],
        args=cfg.get("args", []),
        env=cfg.get("env") or None,
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            parts = []
            for c in result.content:
                if hasattr(c, "text"):
                    parts.append(c.text)
                elif hasattr(c, "data"):
                    parts.append(f"[binary {len(c.data)} bytes]")
            return "\n".join(parts) or "(empty result)"


async def _call_sse(cfg: dict, tool_name: str, arguments: dict) -> str:
    async with sse_client(cfg["url"], headers=cfg.get("headers", {})) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            parts = [c.text for c in result.content if hasattr(c, "text")]
            return "\n".join(parts) or "(empty result)"


# ── Public API ────────────────────────────────────────────────

def _make_executor(server_name: str, cfg: dict, tool_name: str):
    """Return a synchronous tool executor that calls the MCP server."""
    def _execute(params: dict) -> str:
        try:
            if cfg.get("type", "stdio") == "sse":
                return asyncio.run(_call_sse(cfg, tool_name, params))
            else:
                return asyncio.run(_call_stdio(cfg, tool_name, params))
        except Exception as e:
            return f"MCP error ({server_name}/{tool_name}): {e}"
    return _execute


def load_mcp_tools() -> int:
    """
    Connect to all configured MCP servers, register their tools.
    Returns count of tools loaded. Safe to call multiple times (idempotent via _loaded).
    """
    global _loaded
    if _loaded:
        return 0

    configs = _server_configs()
    if not configs:
        return 0

    total = 0
    for server_name, cfg in configs.items():
        try:
            server_type = cfg.get("type", "stdio")
            if server_type == "sse":
                tool_defs = asyncio.run(_list_sse(server_name, cfg))
            else:
                tool_defs = asyncio.run(_list_stdio(server_name, cfg))

            for td in tool_defs:
                # Name the tool "{server}__{tool}" so routing is unambiguous
                qualified = f"{server_name}__{td['name']}"
                # Sanitize: LLMs prefer snake_case names
                qualified = qualified.replace("-", "_")
                register_tool(ToolDef(
                    name=qualified,
                    description=f"[{server_name}] {td['description']}",
                    parameters=td["inputSchema"] or {"type": "object", "properties": {}},
                    execute=_make_executor(server_name, cfg, td["name"]),
                ))
                total += 1

            logger.info("MCP server '%s': %d tools loaded", server_name, len(tool_defs))
        except Exception as e:
            logger.warning("MCP server '%s' unavailable: %s", server_name, e)

    _loaded = True
    return total
