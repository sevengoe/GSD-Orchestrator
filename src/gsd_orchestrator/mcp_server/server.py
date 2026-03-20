"""MCP filesystem server factory.

Creates a FastMCP server instance with security-constrained filesystem access.
All paths are validated against the allowed directories at call time,
with both sides resolved to handle symlinks (e.g., macOS /tmp -> /private/tmp).
"""

import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .tools.files import register_tools

logger = logging.getLogger(__name__)


def create_server(allowed_dirs: list[str]) -> FastMCP:
    """Create a configured FastMCP server instance.

    Args:
        allowed_dirs: Non-empty list of directory paths the server is allowed to access.

    Returns:
        A FastMCP instance with all filesystem tools registered.

    Raises:
        ValueError: If allowed_dirs is empty.
    """
    if not allowed_dirs:
        raise ValueError("allowed_dirs must be non-empty. Pass at least one directory via --allow.")

    resolved_dirs = [Path(d).resolve() for d in allowed_dirs]
    logger.info("Allowed directories: %s", [str(d) for d in resolved_dirs])

    mcp = FastMCP("gsd-filesystem")

    def validate_path(raw: str) -> Path:
        """Validate that the requested path is within an allowed directory.

        Resolves both the requested path and allowed directories to handle
        symlinks (e.g., macOS /tmp -> /private/tmp).

        Args:
            raw: The raw path string from the tool call.

        Returns:
            Resolved Path if it is within an allowed directory.

        Raises:
            PermissionError: If the path is outside all allowed directories.
        """
        p = Path(raw).expanduser().resolve()
        for d in resolved_dirs:
            try:
                p.relative_to(d)
                return p
            except ValueError:
                continue
        raise PermissionError(
            f"Access denied: '{p}' is outside allowed directories. "
            f"Allowed: {[str(d) for d in resolved_dirs]}"
        )

    register_tools(mcp, validate_path)

    return mcp


__all__ = ["create_server"]
