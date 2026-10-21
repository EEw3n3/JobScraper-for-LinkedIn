MIN_DESCRIPTION_LENGTH = 100


def description_status(text):
    """Reject extraction placeholders and access walls before storage or AI."""
    if not isinstance(text, str):
        return "missing_description"
    normalized = " ".join(text.split()).lower()
    if any(marker in normalized for marker in (
        "auth wall", "authwall", "requires authorization to view",
        "sign in to view", "sign in to see", "log in to view",
    )):
        return "auth_required"
    if len(normalized) < MIN_DESCRIPTION_LENGTH or normalized.startswith((
        "could not extract", "couldn't extract", "access denied",
        "too many requests", "verify you are human",
    )):
        return "missing_description"
    return "parsed"
