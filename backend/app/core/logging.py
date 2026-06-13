import logging
import sys

_LOGGER_NAME = "app"


def configure_logging(level: str = "INFO") -> None:
    """Configure the application logger. Idempotent — safe to call repeatedly."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)

    # Avoid duplicate handlers if called more than once (e.g. tests, reload).
    if logger.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
