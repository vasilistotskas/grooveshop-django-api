import logging
import traceback
from typing import override


class LogInfo(logging.Logger):
    def __init__(self, name):
        super().__init__(name)

    @staticmethod
    def configure_logger(logger_name):
        return logging.getLogger(logger_name)

    @staticmethod
    @override
    def debug(msg, logger_name="debug_logger", *args, **kwargs):
        logger = LogInfo.configure_logger(logger_name)
        logger.debug(msg, *args, **kwargs)

    @staticmethod
    @override
    def info(msg, logger_name="info_logger", *args, **kwargs):
        logger = LogInfo.configure_logger(logger_name)
        logger.info(msg, *args, **kwargs)

    @staticmethod
    @override
    def warning(msg, logger_name="warning_logger", *args, **kwargs):
        logger = LogInfo.configure_logger(logger_name)
        logger.warning(msg, *args, **kwargs)

    @staticmethod
    @override
    def error(msg, logger_name="error_logger", *args, **kwargs):
        logger = LogInfo.configure_logger(logger_name)
        logger.error(msg, *args, **kwargs)

    @staticmethod
    @override
    def exception(msg, logger_name="error_logger", *args, **kwargs):
        logger = LogInfo.configure_logger(logger_name)
        stack_trace = traceback.format_exc()
        logger.exception(f"{msg} \n {stack_trace}", *args, **kwargs)
