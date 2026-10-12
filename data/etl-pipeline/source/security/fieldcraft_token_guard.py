import json
import os
import time
from fastapi import Header, HTTPException, status
from .decryption_util import Decryption

# C.5 — crypto constants must come from environment. NO defaults.
# Treat the old hardcoded values as compromised; rotation invalidates old tokens.
try:
    SALT: str = os.environ["FIELDCRAFT_TOKEN_SALT"]
    IV: str = os.environ["FIELDCRAFT_TOKEN_IV"]
except KeyError as exc:
    raise RuntimeError(
        f"Required environment variable missing: {exc.args[0]}. "
        "Set FIELDCRAFT_TOKEN_SALT and FIELDCRAFT_TOKEN_IV before starting the service."
    ) from exc

def require_fieldcraft_claims(x_fieldcraft_token: str = Header(default=None, alias="X-FIELDCRAFT-Token")) -> dict:
    required = os.getenv("FIELDCRAFT_TOKEN_REQUIRED", "true").lower() in ("1", "true", "yes", "on")
    if not required:
        return {}
    if not x_fieldcraft_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-FIELDCRAFT-Token header")
    secret = os.getenv("API_SECRET")
    if not secret:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "API_SECRET not configured")
    try:
        _dec = Decryption(SALT, IV)
        try:
            # v2 (preferred): AES-GCM with a random IV + auth tag.
            plain = _dec.decrypt_v2(x_fieldcraft_token, secret)
        except Exception:
            # v1 fallback: legacy AES-CBC (static IV) tokens still in flight.
            plain = _dec.decrypt(x_fieldcraft_token, secret)
        claims = json.loads(plain)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid FIELDCRAFT token")
    if claims.get("expiresAt", 0) < time.time():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "FIELDCRAFT token expired")
    return claims


def resolve_environment(claims: dict) -> str:
    """Resolve the originating FIELDCRAFT environment (dev/qa/stag/sb/dm/prod) from decrypted
    X-FIELDCRAFT-Token claims. Returns "" when unresolved — callers treat that as "use the
    default/startup Mongo client," not an error.
    """
    return str((claims or {}).get("environment") or (claims or {}).get("env") or "").strip().lower()
