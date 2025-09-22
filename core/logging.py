import os
import logging


class HostnameFilter(logging.Filter):
    def filter(self, record):
        record.hostname = os.getenv("HOSTNAME", "unknown")
        return True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
