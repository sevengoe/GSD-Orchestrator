"""GSD MCP filesystem server package.

Provides a FastMCP-based server with security-constrained filesystem access.
"""

from .server import create_server

__all__ = ["create_server"]
