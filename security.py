"""Small helpers to avoid leaking Toss Invest secrets in logs."""


SENSITIVE_KEYS = set([
    "client_id",
    "client_secret",
    "access_token",
    "authorization",
    "x-tossinvest-account",
    "accountseq",
    "account_seq",
])


def mask_secret(value, visible=4):
    text = str(value or "").strip()
    if not text:
        return ""
    if visible <= 0:
        return "*" * len(text)
    if len(text) <= visible:
        return "*" * len(text)
    return "{}{}".format("*" * (len(text) - visible), text[-visible:])


def sanitize_payload(value):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                sanitized[key] = mask_secret(item, visible=0)
            else:
                sanitized[key] = sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    return value
