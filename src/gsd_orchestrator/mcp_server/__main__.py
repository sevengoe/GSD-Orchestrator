import argparse
import logging
import sys
from pathlib import Path

import yaml

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
    parser.add_argument("--config", dest="config_path",
                        help="Path to config.yaml — reads mcp.allowed_directories")
    args = parser.parse_args()

    allowed_dirs = list(args.allowed_dirs or [])

    if args.config_path:
        config_file = Path(args.config_path)
        if not config_file.exists():
            print(f"Error: config file not found: {args.config_path}", file=sys.stderr)
            sys.exit(1)
        with open(config_file) as f:
            cfg = yaml.safe_load(f)
        base = config_file.parent.resolve()
        cfg_dirs = cfg.get("mcp", {}).get("allowed_directories") or []
        if not cfg_dirs:
            cfg_dirs = [cfg.get("claude", {}).get("working_dir", "workspace")]
        for d in cfg_dirs:
            d = Path(d).expanduser()
            if not d.is_absolute():
                d = base / d
            allowed_dirs.append(str(d))

    if not allowed_dirs:
        print("Error: at least one --allow directory is required", file=sys.stderr)
        sys.exit(1)

    mcp = create_server(allowed_dirs)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
