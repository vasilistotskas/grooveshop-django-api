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

    if use_digits:
        password = random.choice(string.digits) + "".join(
            random.choice(chars) for _ in range(length - 1)
        )
    else:
        password = "".join(random.choice(chars) for _ in range(length))

    password_list = list(password)
    random.shuffle(password_list)
    password = "".join(password_list)

    return password
