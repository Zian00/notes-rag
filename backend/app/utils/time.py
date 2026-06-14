from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware current UTC time (single source for the app)."""
    return datetime.now(tz=UTC)
