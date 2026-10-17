import logging
import sys

from app.core.context import current_request_id


class _RequestIdFilter(logging.Filter):
    """Zet de request-ID in elke logregel, zodat je een fout terugvindt via de X-Request-ID."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id.get()
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    logging.basicConfig(
        level=level.upper(),
        handlers=[handler],
        format="%(asctime)s %(levelname)s [%(request_id)s] %(name)s %(message)s",
    )
