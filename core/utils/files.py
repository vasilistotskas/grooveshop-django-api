import re


def sanitize_filename(filename: str):
    filename = re.sub(r"[^A-Za-z0-9_.-]", "", filename)
    filename = re.sub(r"\.+", ".", filename).strip(".")
    filename = filename[:50]
    return filename
