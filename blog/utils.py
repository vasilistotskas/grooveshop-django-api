import re


def calculate_reading_time(content: str, words_per_minute: int = 200) -> int:
    if not content:
        return 0

    word_count = len(re.findall(r"\w+", content))
    reading_time = max(1, word_count // words_per_minute)
    return reading_time
