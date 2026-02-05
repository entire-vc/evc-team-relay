from __future__ import annotations

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, IntegrityError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle HTTP exceptions with consistent error response format."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.status_code,
                "message": exc.detail,
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle request validation errors with detailed field information."""
    # Convert errors to JSON-serializable format
    errors = []
    for error in exc.errors():
        error_dict = {
            "type": error["type"],
            "loc": error["loc"],
            "msg": error["msg"],
            "input": error.get("input"),
        }
        # Convert ctx values to strings if they exist
        if "ctx" in error:
            error_dict["ctx"] = {k: str(v) for k, v in error["ctx"].items()}
        errors.append(error_dict)

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": 422,
                "message": "Validation error",
                "details": errors,
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """Handle database integrity constraint violations (unique, foreign key, etc.)."""
    settings = get_settings()

    # Log the error with context
    logger.error(
        "Database integrity error",
        extra={
            "error_type": "IntegrityError",
            "error_message": str(exc),
            "endpoint": request.url.path,
            "method": request.method,
            "request_id": getattr(request.state, "request_id", None),
        },
    )

    # Extract user-friendly message
    error_msg = "Database constraint violation"
    details = None

    exc_str = str(exc.orig) if exc.orig else str(exc)
    if "unique constraint" in exc_str.lower() or "duplicate" in exc_str.lower():
        error_msg = "A record with this value already exists"
        if hasattr(settings, "debug_mode") and settings.debug_mode:
            details = exc_str
    elif "foreign key constraint" in exc_str.lower():
        error_msg = "Referenced record does not exist"
        if hasattr(settings, "debug_mode") and settings.debug_mode:
            details = exc_str
    elif "not null constraint" in exc_str.lower():
        error_msg = "Required field is missing"
        if hasattr(settings, "debug_mode") and settings.debug_mode:
            details = exc_str

    content = {
        "error": {
            "code": 409,
            "message": error_msg,
            "request_id": getattr(request.state, "request_id", None),
        }
    }

    if details:
        content["error"]["details"] = details

    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=content)


async def data_error_handler(request: Request, exc: DataError) -> JSONResponse:
    """Handle database data type and format errors."""
    settings = get_settings()

    logger.error(
        "Database data error",
        extra={
            "error_type": "DataError",
            "error_message": str(exc),
            "endpoint": request.url.path,
            "method": request.method,
            "request_id": getattr(request.state, "request_id", None),
        },
    )

    error_msg = "Invalid data format or type"
    details = None

    if hasattr(settings, "debug_mode") and settings.debug_mode:
        details = str(exc.orig) if exc.orig else str(exc)

    content = {
        "error": {
            "code": 400,
            "message": error_msg,
            "request_id": getattr(request.state, "request_id", None),
        }
    }

    if details:
        content["error"]["details"] = details

    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=content)


async def operational_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
    """Handle database connection and operational errors."""
    settings = get_settings()

    logger.error(
        "Database operational error",
        extra={
            "error_type": "OperationalError",
            "error_message": str(exc),
            "endpoint": request.url.path,
            "method": request.method,
            "request_id": getattr(request.state, "request_id", None),
        },
    )

    error_msg = "Database service temporarily unavailable"
    details = None

    if hasattr(settings, "debug_mode") and settings.debug_mode:
        details = str(exc.orig) if exc.orig else str(exc)

    content = {
        "error": {
            "code": 503,
            "message": error_msg,
            "request_id": getattr(request.state, "request_id", None),
        }
    }

    if details:
        content["error"]["details"] = details

    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=content)


async def database_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Handle generic database errors (fallback)."""
    settings = get_settings()

    logger.error(
        "Database error",
        extra={
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "endpoint": request.url.path,
            "method": request.method,
            "request_id": getattr(request.state, "request_id", None),
        },
    )

    content = {
        "error": {
            "code": 503,
            "message": "Database error occurred",
            "request_id": getattr(request.state, "request_id", None),
        }
    }

    if hasattr(settings, "debug_mode") and settings.debug_mode:
        content["error"]["details"] = str(exc)

    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=content)


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected errors."""
    logger.exception(
        "Unexpected error",
        extra={
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "endpoint": request.url.path,
            "method": request.method,
            "request_id": getattr(request.state, "request_id", None),
        },
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": 500,
                "message": "Internal server error",
                "request_id": getattr(request.state, "request_id", None),
            }
        },
    )
