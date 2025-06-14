from disposable_email_domains import blocklist as DISPOSABLE_BLOCKLIST


def is_disposable_domain(domain: str) -> bool:
    domain = domain.lower()
    if domain in DISPOSABLE_BLOCKLIST:
        return True
    parts = domain.split(".")
    for i in range(1, len(parts) - 1):
        if ".".join(parts[i:]) in DISPOSABLE_BLOCKLIST:
            return True
    return False
