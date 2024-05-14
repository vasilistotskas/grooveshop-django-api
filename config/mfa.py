from os import getenv

MFA_ADAPTER = "allauth.mfa.adapter.DefaultMFAAdapter"
MFA_RECOVERY_CODE_COUNT = 1
MFA_TOTP_PERIOD = 60
MFA_TOTP_DIGITS = 6
MFA_TOTP_ISSUER = getenv("MFA_TOTP_ISSUER", "localhost")
