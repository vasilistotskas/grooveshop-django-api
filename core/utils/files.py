import re
import unicodedata
import uuid

from search.transliteration import greek_to_greeklish

MAX_FILENAME_LENGTH = 50

_UNSAFE_STEM_RE = re.compile(r"[^A-Za-z0-9_.-]")
_UNSAFE_EXTENSION_RE = re.compile(r"[^A-Za-z0-9]")
_DOTS_RE = re.compile(r"\.+")


def sanitize_filename(filename: str) -> str:
    """A storage-safe ASCII name for an uploaded file.

    Keeps the extension whatever happens to the rest: the name used to
    be reduced as one string, so ``εικόνα.png`` lost every letter,
    became ``.png``, had its dot stripped and was stored as a file
    called ``png`` — an image URL that no longer said it was an image.

    Greek is transliterated (the stores are Greek, and so are most
    upload names) and accents are folded, so the stem stays readable. A
    stem with nothing left after that gets a random one; a file has to
    be called something.
    """
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem, _, extension = name.lstrip(".").rpartition(".")
    if not stem:
        stem, extension = extension, ""

    extension = _UNSAFE_EXTENSION_RE.sub("", extension)
    ascii_stem = (
        unicodedata.normalize("NFKD", greek_to_greeklish(stem))
        .encode("ascii", "ignore")
        .decode()
    )
    stem = _DOTS_RE.sub(".", _UNSAFE_STEM_RE.sub("", ascii_stem)).strip(".")

    suffix = f".{extension}" if extension else ""
    stem = stem[: MAX_FILENAME_LENGTH - len(suffix)].rstrip(".")
    return f"{stem or uuid.uuid4().hex[:12]}{suffix}"
