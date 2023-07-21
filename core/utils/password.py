import random
import string


def generate_random_password(length=12, use_digits=True, use_special_chars=True):
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

    password = "".join(random.choice(chars) for _ in range(length))
    return password
