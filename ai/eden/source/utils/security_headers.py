from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

# Sane defaults for a JSON API with no browser-rendered content of its own --
# not a substitute for the frontend's own CSP, just closes the standard
# missing-header findings for this service's direct responses.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}

# Body-size cap: this API only ever accepts small JSON payloads (an
# utterance, session context) -- no file uploads. A large body is either a
# misbehaving client or abuse, not a legitimate request.
MAX_BODY_BYTES = int(1_000_000)  # 1 MB


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_BODY_BYTES:
                    return PlainTextResponse("Request body too large", status_code=413)
            except ValueError:
                pass  # malformed header -- let normal request handling reject it

        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response
