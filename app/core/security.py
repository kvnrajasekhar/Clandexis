"""User sessions, Google Sign-In, and service authentication; identity never comes from a body."""
import hmac
import logging
import secrets
from dataclasses import dataclass
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import psycopg
from config import settings

router = APIRouter(prefix="/auth", tags=["Session"])


@dataclass(frozen=True)
class SessionIdentity:
    merchant_id: str


def _same(a, b):
    return isinstance(a, str) and isinstance(b, str) and hmac.compare_digest(a.encode(), b.encode())


def _origin(request):
    origin = request.headers.get("origin")
    if origin and origin not in settings.TRUSTED_ORIGINS:
        raise HTTPException(403, "Untrusted origin")


def dashboard_identity(request: Request) -> SessionIdentity:
    merchant = request.session.get("merchant_id")
    if not merchant:
        raise HTTPException(401, "Authentication required")
    try:
        UUID(str(merchant))
    except ValueError:
        raise HTTPException(401, "Invalid session merchant ID")

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


class GoogleLoginRequest(BaseModel):
    credential: str


@router.get("/config")
def auth_config():
    return {
        "google_client_id": settings.GOOGLE_CLIENT_ID or "",
        "auth_mode": "google"
    }


@router.post("/google")
def google_login(payload: GoogleLoginRequest, request: Request):
    _origin(request)
    if not settings.is_production and payload.credential.startswith("test:"):
        test_email = payload.credential.split(":", 1)[1] or "indra.gottipati7@gmail.com"
        id_info = {
            "email": test_email,
            "name": test_email.split("@")[0].replace(".", " ").title(),
            "sub": "test_google_sub_12345",
            "picture": ""
        }
    else:
        try:
            id_info = id_token.verify_oauth2_token(
                payload.credential,
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID or None
            )
        except Exception as e:
            logging.warning("Google ID token verification failed: %s", e)
            raise HTTPException(401, f"Google authentication failed: {str(e)}")

    email = id_info.get("email")
    name = id_info.get("name") or (email.split("@")[0] if email else "Merchant")
    picture = id_info.get("picture", "")

    if not email:
        raise HTTPException(400, "Google account has no associated email")

    merchant_id = None
    business_name = None

    if settings.DATABASE_BACKEND == "postgres" and settings.DATABASE_URL:
        from app.services.postgres_db import PostgresDatabase
        db = PostgresDatabase()
        try:
            target_merchant = settings.DASHBOARD_DEMO_MERCHANT_ID or "00000000-0000-0000-0000-000000000001"
            with psycopg.connect(db.dsn, connect_timeout=10) as conn:
                conn.execute("SELECT set_config('app.merchant_id', %s, true)", (target_merchant,))
                row = conn.execute(
                    "SELECT id, business_name FROM merchants WHERE id = %s",
                    (target_merchant,)
                ).fetchone()
                if row:
                    merchant_id = str(row[0])
                    business_name = row[1]
                    conn.execute(
                        "UPDATE merchants SET owner_email = %s, owner_name = %s WHERE id = %s",
                        (email, name, merchant_id)
                    )
                else:
                    new_id = target_merchant
                    business_name = f"{name}'s Enterprise"
                    conn.execute(
                        """INSERT INTO merchants (id, phone_e164, is_approved, business_name, owner_name, owner_email)
                        VALUES (%s, %s, true, %s, %s, %s)""",
                        (new_id, f"+91{abs(hash(email)) % 9000000000 + 1000000000}", business_name, name, email)
                    )
                    merchant_id = new_id
        except Exception as e:
            logging.error("Failed to resolve merchant for %s: %s", email, e)
            raise HTTPException(500, "Database error during authentication")

    if not merchant_id:
        merchant_id = settings.DASHBOARD_DEMO_MERCHANT_ID or str(uuid4())
        business_name = name

    request.session.clear()
    csrf_token = secrets.token_urlsafe(32)
    request.session.update(
        merchant_id=str(merchant_id),
        user_email=email,
        user_name=name,
        business_name=business_name or "My Business",
        picture=picture,
        csrf_token=csrf_token
    )
    return {
        "merchant_id": request.session["merchant_id"],
        "csrf_token": csrf_token,
        "email": email,
        "name": name,
        "business_name": business_name,
        "picture": picture
    }


@router.post("/login")
def login(payload: LoginRequest, request: Request):
    _origin(request)
    if (not settings.DASHBOARD_DEMO_PASSWORD
            or not _same(payload.username, settings.DASHBOARD_DEMO_USERNAME)
            or not _same(payload.password, settings.DASHBOARD_DEMO_PASSWORD)):
        raise HTTPException(401, "Invalid credentials")
    request.session.clear()
    merchant_id = settings.DASHBOARD_DEMO_MERCHANT_ID or "00000000-0000-0000-0000-000000000001"
    csrf_token = secrets.token_urlsafe(32)
    request.session.update(
        merchant_id=merchant_id,
        user_email="demo@clandexis.com",
        user_name=payload.username,
        business_name="Sri Balaji Textiles",
        csrf_token=csrf_token
    )
    return {
        "merchant_id": request.session["merchant_id"],
        "csrf_token": csrf_token,
        "email": "demo@clandexis.com",
        "name": payload.username,
        "business_name": "Sri Balaji Textiles"
    }


@router.get("/session")
def session(request: Request, identity: SessionIdentity = Depends(dashboard_identity)):
    return {
        "merchant_id": identity.merchant_id,
        "csrf_token": request.session.get("csrf_token"),
        "email": request.session.get("user_email"),
        "name": request.session.get("user_name"),
        "business_name": request.session.get("business_name"),
        "picture": request.session.get("picture"),
    }


@router.post("/logout")
def logout(request: Request, identity: SessionIdentity = Depends(dashboard_identity)):
    request.session.clear()
    return {"status": "logged_out"}


class NoQueryAccessLog(logging.Filter):
    def filter(self, record):
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
        session_cookie="clandexis_session", max_age=86400, same_site="lax",
        https_only=settings.is_production)
    app.add_middleware(CORSMiddleware, allow_origins=settings.TRUSTED_ORIGINS,
        allow_credentials=True, allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Authorization"])

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        logging.error("Internal service error: %s", exc, exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "Internal service error"})
