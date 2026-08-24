from __future__ import annotations
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.core.security import decode_access_token, extract_bearer_token


class TenantMiddleware(BaseHTTPMiddleware):
    """
    questo middleware arricchisce solo il request.state per uso nei log e nel rate limiter
    """

    PUBLIC_PATHS = {"/health", "/ready", "/metrics", "/docs", "/redoc", "/openapi.json"}

    async def dispatch(self, request: Request, call_next) -> Response:
        #utilizzo request.state che è uno spazio vuoto per salvare dati custom
        request.state.tenant_id = None
        request.state.tenant_slug = None
        request.state.user_id = None
        request.state.user_role = None
        request.state.user_email = None

        if request.url.path in self.PUBLIC_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        token = extract_bearer_token(auth_header)
        if token:
            payload = decode_access_token(token)
            if payload and not payload.get("is_platform"):

                request.state.tenant_id = payload.get("tenant_id")
                request.state.tenant_slug = payload.get("tenant_slug")
                request.state.user_id = payload.get("sub")
                request.state.user_role = payload.get("role")
                request.state.user_email = payload.get("email")
        elif request.headers.get("X-API-Key"):
            # Senza questo ramo, le richieste autenticate solo via X-API-Key restavano con
            # tenant_id/user_id = None qui, e RateLimitMiddleware (che gira dopo, leggendo
            # request.state) le lasciava passare senza applicare alcun rate limit.
            from app.api.deps import _validate_api_key
            context = await _validate_api_key(request.headers["X-API-Key"])
            if context:
                request.state.tenant_id = context.tenant_id
                request.state.tenant_slug = context.tenant_slug
                request.state.user_id = context.user_id
                request.state.user_role = context.user_role
                request.state.user_email = context.user_email
        return await call_next(request)


