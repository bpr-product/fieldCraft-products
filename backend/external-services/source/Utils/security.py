import os

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

API_KEY_ENV_VAR = "API_SECRET"
API_KEY_REQUIRED_ENV_VAR = "API_SECRET_REQUIRED"

api_key_header = APIKeyHeader(name="api-secret", auto_error=False)


def verify_api_key(secret_key: str = Security(api_key_header)) -> None:
    """Verify the api-secret header against the configured env var."""

    # If disabled, skip verification (dev/testing)
    required = os.getenv(API_KEY_REQUIRED_ENV_VAR, "true").lower() in ("1", "true", "yes", "on")
    if not required:
        return

    expected = os.getenv(API_KEY_ENV_VAR)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Server configuration error: {API_KEY_ENV_VAR} not set.",
        )

    if not secret_key or secret_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid or missing API key.",
        )
