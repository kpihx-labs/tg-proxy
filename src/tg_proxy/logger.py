"""
System logger for tg-proxy — logs to stderr for systemd/journald capture.

No file management: systemd/journald handles log rotation and retention.
"""

import logging
import sys

logger = logging.getLogger("tg_proxy")


def setup_logging(level: str = "WARNING"):
    """Log to stderr — systemd/journald captures it automatically.

    In a terminal → visible on stderr (not mixed with stdout JSON).
    In a systemd service → captured by journalctl.
    """
    logger.setLevel(getattr(logging, level.upper(), logging.WARNING))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(handler)
    return logger
