import random
import string


def generate_random_password(
    length=12, use_digits=False, use_special_chars=False
):
    chars = string.ascii_letters

    if use_digits:
        chars += string.digits

    if use_special_chars:
        chars += string.punctuation

    password_parts = []

    if use_digits:
        password_parts.append(random.choice(string.digits))

    if use_special_chars:
        password_parts.append(random.choice(string.punctuation))

    remaining_length = length - len(password_parts)
    password_parts.extend(random.choice(chars) for _ in range(remaining_length))

    random.shuffle(password_parts)
    password = "".join(password_parts)

    return password
