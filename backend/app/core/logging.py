import logging
import re
import sys

from app.core.context import current_request_id


class _RequestIdFilter(logging.Filter):
    """Zet de request-ID in elke logregel, zodat je een fout terugvindt via de X-Request-ID."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id.get()
        return True


_TOKEN_RE = re.compile(r"(access_token=)[^&\s\"]+")


class _RedactTokenFilter(logging.Filter):
    """De Bitwarden-apps zetten hun access token in de URL van de notificatie-hub
    (?access_token=...); uvicorn logt die URL. Het token hoort niet in de logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "access_token=" in message:
            record.msg, record.args = _TOKEN_RE.sub(r"\1***", message), None
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    logging.basicConfig(
        level=level.upper(),
        handlers=[handler],
        format="%(asctime)s %(levelname)s [%(request_id)s] %(name)s %(message)s",
    )
    for name in ("uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addFilter(_RedactTokenFilter())
