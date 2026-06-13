import logging

from app.core.logging import configure_logging


def test_configure_logging_sets_level():
    configure_logging(level="INFO")
    assert logging.getLogger("app").level == logging.INFO


def test_configure_logging_is_idempotent():
    configure_logging(level="INFO")
    configure_logging(level="DEBUG")
    logger = logging.getLogger("app")
    # No duplicate handlers on repeat calls.
    assert len(logger.handlers) == 1
    assert logger.level == logging.DEBUG
