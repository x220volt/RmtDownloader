"""Bounded child-process cleanup for download engines."""

import logging
import subprocess

logger = logging.getLogger(__name__)


def reap_process(process, timeout=3):
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            logger.exception("Could not reap download process after kill")
    except OSError:
        logger.exception("Could not reap download process")
