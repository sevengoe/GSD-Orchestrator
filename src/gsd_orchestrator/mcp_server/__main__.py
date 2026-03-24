import argparse
import logging
import sys

from .server import create_server


def main():
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="GSD MCP Filesystem Server")
    parser.add_argument("--allow", action="append", dest="allowed_dirs",
                        help="Allowed directory (can specify multiple)")
    args = parser.parse_args()

    if not args.allowed_dirs:
        print("Error: at least one --allow directory is required", file=sys.stderr)
        sys.exit(1)

    mcp = create_server(args.allowed_dirs)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
