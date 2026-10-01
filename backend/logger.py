import json
import logging
from contextvars import ContextVar
from functools import wraps
from time import perf_counter
from uuid import uuid4

logger = logging.getLogger("summarizer")
logger.setLevel(logging.INFO)

# Keep JSON output independent of Lambda's text formatter and other functions.
_event_logger = logging.getLogger("summarizer.observability")
_event_logger.setLevel(logging.INFO)
_event_logger.propagate = False
if not _event_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    _event_logger.addHandler(handler)

_request_id = ContextVar("request_id", default=None)


def log_event(event, level="info", **fields):
    """Callers supply only static labels, timings, counts and exception types."""
    record = {"event": event, "level": level, "request_id": _request_id.get()}
    record.update(fields)
    _event_logger.log(getattr(logging, level.upper()), json.dumps(record))


def observe_operation(operation):
    """Time a retrieval/model call without inspecting its inputs or content."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            started = perf_counter()
            outcome = "error"
            log_event(operation + "_started")
            try:
                result = function(*args, **kwargs)
                outcome = "returned" if result else "no_result"
                return result
            except Exception as error:
                log_event(operation + "_error", level="error",
                          error_type=type(error).__name__)
                raise
            finally:
                log_event(operation + "_completed", outcome=outcome,
                          duration_ms=round((perf_counter() - started) * 1000, 3))
        return wrapped
    return decorate


def observe_request(function):
    """Cover every return path and reset correlation for warm Lambda reuse."""
    @wraps(function)
    def wrapped(event, context):
        token = _request_id.set(getattr(context, "aws_request_id", None) or str(uuid4()))
        started = perf_counter()
        status_code = 500
        try:
            log_event("request_started")
            response = function(event, context)
            status_code = response["statusCode"]
            return response
        except Exception as error:
            log_event("request_error", level="error", error_type=type(error).__name__)
            raise
        finally:
            try:
                log_event("request_completed", status_code=status_code,
                          duration_ms=round((perf_counter() - started) * 1000, 3))
            finally:
                _request_id.reset(token)
    return wrapped
