"""Keep observer failures separate from transfer outcomes."""

import logging
from functools import wraps

logger = logging.getLogger(__name__)


def safe_callback(callback):
    if callback is None:
        return None

    @wraps(callback)
    def notify(*args, **kwargs):
        try:
            return callback(*args, **kwargs)
        except Exception:
            logger.exception("Download observer failed")
            return None

    return notify
