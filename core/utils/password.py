import random
import string


def generate_random_password(
    length=12, use_digits=False, use_special_chars=False
) -> str:
    """Generate a random password with the specified length.

    Args:
        length (int): The length of the password. Default is 12.
        use_digits (bool): Include digits in the password. Default is True.
        use_special_chars (bool): Include special characters in the password. Default is True.

    Returns
        str: The generated random password.

    """
    chars = string.ascii_letters

    if use_digits:
        chars += string.digits

    if use_special_chars:
        chars += string.punctuation

    # Ensure at least one digit if use_digits is True
    if use_digits:
        password = random.choice(string.digits) + "".join(
            random.choice(chars) for _ in range(length - 1)
        )
    else:
        password = "".join(random.choice(chars) for _ in range(length))

    # Shuffle the characters in the password
    password_list = list(password)
    random.shuffle(password_list)
    password = "".join(password_list)

    return password
