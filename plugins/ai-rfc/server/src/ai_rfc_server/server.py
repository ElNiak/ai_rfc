"""The ``arfc`` MCP server: FastMCP over the shared tool surface."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .tools import ALL_TOOLS

mcp = FastMCP("arfc")

for tool in ALL_TOOLS:
    mcp.tool()(tool)


def main() -> None:
    """Run the stdio server."""
    mcp.run()


if __name__ == "__main__":
    main()
