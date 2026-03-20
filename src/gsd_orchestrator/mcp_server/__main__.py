"""CLI entry point for the GSD MCP filesystem server.

Usage:
    python -m gsd_orchestrator.mcp_server --allow /path/to/dir [--allow /other/dir]

All output to stdout is reserved for the JSON-RPC protocol stream.
Logging is sent to stderr only.
"""

import argparse
import logging
import sys

from .server import create_server


def main() -> None:
    """Parse arguments and start the MCP server."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="python -m gsd_orchestrator.mcp_server",
        description="GSD MCP filesystem server — secure file access via MCP protocol.",
    )
    parser.add_argument(
        "--allow",
        action="append",
        dest="allowed_dirs",
        metavar="DIR",
        help="Allow access to this directory (can be specified multiple times).",
    )
    parser.add_argument(
        "--config",
        dest="config_file",
        metavar="FILE",
        help="(Reserved) Config file path — not implemented in Phase 1.",
    )

    args = parser.parse_args()

    allowed_dirs: list[str] = args.allowed_dirs or []

    if not allowed_dirs:
        print(
            "Error: at least one --allow DIR argument is required.",
            file=sys.stderr,
        )
        sys.exit(1)

    logger = logging.getLogger(__name__)
    logger.info("Starting GSD MCP filesystem server with allowed dirs: %s", allowed_dirs)

    mcp = create_server(allowed_dirs)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
