import logging

from django.utils.datetime_safe import time

logger = logging.getLogger(__name__)


def timing(function: callable) -> callable:
    def wrap(*args, **kwargs) -> callable:
        start_time = time.time()
        result = function(*args, **kwargs)
        end_time = time.time()
        duration = (end_time - start_time) * 1000.0
        f_name = function.__name__
        logger.info("{} took {:.3f} ms".format(f_name, duration))

        return result

    return wrap


def log_db_queries(function: callable) -> callable:
    from django.db import connection

    def wrap(*args, **kwargs) -> callable:
        start_time = time.time()
        result = function(*args, **kwargs)
        print("\n\n")
        print("-" * 80)
        print("db queries log for %s:\n {}:" % function.__name__)
        print("TOTAL COUNT: %s" % len(connection.queries))
        for query in connection.queries:
            print("%s: %s\n" % (query["time"], query["sql"]))
        end_time = time.time()
        duration = (end_time - start_time) * 1000.0
        print("\n Total Time: {:.3f} ms".format(duration))
        print("-" * 80)
        return result

    return wrap
