"""Restricted demo sessions and service authentication; identity never comes from a body."""
import hmac
import logging
import secrets
from dataclasses import dataclass
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from config import settings

router = APIRouter(prefix="/auth", tags=["Session"])


@dataclass(frozen=True)
class SessionIdentity:
    merchant_id: str


def _same(a, b):
    return isinstance(a, str) and isinstance(b, str) and hmac.compare_digest(a.encode(), b.encode())


def _enabled():
    return (len(settings.DASHBOARD_SESSION_SECRET) >= 32
            and len(settings.DASHBOARD_DEMO_PASSWORD) >= 16
            and bool(settings.DASHBOARD_DEMO_MERCHANT_ID)
            and "/" not in settings.DASHBOARD_DEMO_MERCHANT_ID)


def _origin(request):
    origin = request.headers.get("origin")
    if origin and origin not in settings.TRUSTED_ORIGINS:
        raise HTTPException(403, "Untrusted origin")


def dashboard_identity(request: Request) -> SessionIdentity:
    merchant = request.session.get("merchant_id")
    if not _enabled() or merchant != settings.DASHBOARD_DEMO_MERCHANT_ID:
        raise HTTPException(401, "Authentication required")
    requested = request.query_params.getlist("merchant_id")
    if any(value != merchant for value in requested):
        raise HTTPException(403, "Merchant access denied")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        _origin(request)
        if not _same(request.headers.get("x-csrf-token"), request.session.get("csrf_token")):
            raise HTTPException(403, "Invalid CSRF token")
    return SessionIdentity(merchant)


def require_service(request: Request):
    configured = settings.INTERNAL_SEND_TOKEN
    if len(configured) < 32 or not _same(request.headers.get("authorization"), f"Bearer {configured}"):
        raise HTTPException(401, "Service authentication required")


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(payload: LoginRequest, request: Request):
    _origin(request)
    if (not _enabled() or not _same(payload.username, settings.DASHBOARD_DEMO_USERNAME)
            or not _same(payload.password, settings.DASHBOARD_DEMO_PASSWORD)):
        raise HTTPException(401, "Invalid credentials")
    request.session.clear()
    request.session.update(merchant_id=settings.DASHBOARD_DEMO_MERCHANT_ID, csrf_token=secrets.token_urlsafe(32))
    return {"merchant_id": request.session["merchant_id"], "csrf_token": request.session["csrf_token"]}


@router.get("/session")
def session(request: Request, identity: SessionIdentity = Depends(dashboard_identity)):
    return {"merchant_id": identity.merchant_id, "csrf_token": request.session["csrf_token"]}


@router.post("/logout")
def logout(request: Request, identity: SessionIdentity = Depends(dashboard_identity)):
    request.session.clear()
    return {"status": "logged_out"}


class NoQueryAccessLog(logging.Filter):
    def filter(self, record):
        # Uvicorn normally logs full request URLs, including hub.verify_token.
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            args[2] = str(args[2]).partition("?")[0]
            record.args = tuple(args)
        return True


def install_security(app):
    logging.getLogger("uvicorn.access").addFilter(NoQueryAccessLog())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app.add_middleware(SessionMiddleware,
        secret_key=settings.DASHBOARD_SESSION_SECRET or secrets.token_urlsafe(48),
        session_cookie="clandexis_session", max_age=3600, same_site="strict",
        https_only=settings.is_production)
    app.add_middleware(CORSMiddleware, allow_origins=settings.TRUSTED_ORIGINS,
        allow_credentials=True, allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Authorization"])

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # FastAPI's default includes input values (including passwords) in errors.
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return JSONResponse(status_code=500, content={"detail": "Internal service error"})
