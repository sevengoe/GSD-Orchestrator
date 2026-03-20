import logging
from logging.handlers import TimedRotatingFileHandler

from .config import Config
from .orchestrator import Orchestrator


def main():
    config = Config.load()

    config.log_dir.mkdir(parents=True, exist_ok=True)
    log_file = config.log_dir / "gsd-orchestrator.log"

    file_handler = TimedRotatingFileHandler(
        str(log_file),
        when="midnight",
        interval=1,
        backupCount=config.log_retention_days,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            file_handler,
        ],
    )

    logging.getLogger("httpx").setLevel(logging.WARNING)

    orchestrator = Orchestrator(config)
    orchestrator.run()


if __name__ == "__main__":
    main()
