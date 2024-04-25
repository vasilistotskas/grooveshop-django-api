import html
import re
from typing import Optional

RE_HTML_TAGS = re.compile("<.*?>")
RE_WHITESPACE_AND_PUNCTUATION = re.compile(r"[,.?!;:\'\"()\[\]]|\s+|\xa0")


def preprocess_text(text: Optional[str]) -> str:
    if not text:
        return ""

    text = re.sub(RE_HTML_TAGS, "", text)
    text = html.unescape(text)
    text = re.sub(RE_WHITESPACE_AND_PUNCTUATION, " ", text)
    return text.strip()
