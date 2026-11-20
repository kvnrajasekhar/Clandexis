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
import json
import re
import psycopg
from config import settings

router = APIRouter(prefix="/auth", tags=["Session"])


def normalize_phone(phone: str) -> str:
    cleaned = re.sub(r"[\s\-\(\)]", "", (phone or "").strip())
    if re.match(r"^[6-9]\d{9}$", cleaned):
        cleaned = f"+91{cleaned}"
    elif re.match(r"^0[6-9]\d{9}$", cleaned):
        cleaned = f"+91{cleaned[1:]}"
    elif not cleaned.startswith("+") and cleaned.startswith("91") and len(cleaned) == 12:
        cleaned = f"+{cleaned}"
    if not re.match(r"^\+[1-9]\d{7,14}$", cleaned):
        raise ValueError("Please enter a valid mobile number with country code (e.g., +91 98765 43210 or 10-digit mobile number)")
    return cleaned


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


class OnboardRequest(BaseModel):
    name: str
    phone_number: str
    business_name: str


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
    phone_number = None
    needs_onboarding = True

    DEMO_MERCHANT_ID = "00000000-0000-0000-0000-000000000001"
    if settings.DATABASE_BACKEND == "postgres" and settings.DATABASE_URL:
        from app.services.postgres_db import PostgresDatabase
        db = PostgresDatabase()
        try:
            with psycopg.connect(db.dsn, connect_timeout=5) as conn:
                # 1. Query for an existing merchant owned by this Google email
                row = conn.execute(
                    "SELECT id, business_name, phone_e164, tax_address FROM merchants WHERE owner_email = %s LIMIT 1",
                    (email,)
                ).fetchone()

                # If the demo merchant was previously mistakenly linked to this email, unlink it
                if row and str(row[0]) == DEMO_MERCHANT_ID:
                    conn.execute(
                        "UPDATE merchants SET owner_email = NULL, owner_name = 'Demo Merchant' WHERE id = %s",
                        (DEMO_MERCHANT_ID,)
                    )
                    conn.commit()
                    row = None

                tax_data = row[3] if row and isinstance(row[3], dict) else {}
                if not picture and tax_data.get("picture"):
                    picture = tax_data["picture"]
                updated_tax = {**tax_data, "picture": picture} if picture else tax_data
                tax_json = json.dumps(updated_tax) if updated_tax else None

                if row:
                    merchant_id = str(row[0])
                    business_name = row[1]
                    phone_number = str(row[2]) if row[2] else None
                    conn.execute(
                        "UPDATE merchants SET owner_name = %s, tax_address = %s WHERE id = %s",
                        (name, tax_json, merchant_id)
                    )
                    conn.commit()
                    # Check if the user has completed explicit onboarding
                    is_onboarded = bool(tax_data.get("onboarded", False))
                    has_valid_phone = bool(phone_number and not phone_number.startswith("+910000"))
                    has_biz_name = bool(business_name and "Business" not in business_name)
                    if is_onboarded and has_valid_phone:
                        needs_onboarding = False
                    else:
                        needs_onboarding = True
                else:
                    # New user from Google Sign In: prompt for mobile number and business name
                    needs_onboarding = True
        except Exception as e:
            logging.warning("Database lookup during auth for %s: %s (using graceful fallback)", email, e)
            needs_onboarding = True

    request.session.clear()
    csrf_token = secrets.token_urlsafe(32)
    request.session.update(
        merchant_id=str(merchant_id or ""),
        user_email=email,
        user_name=name,
        business_name=business_name or "",
        picture=picture or "",
        phone_number=phone_number or "",
        needs_onboarding=needs_onboarding,
        csrf_token=csrf_token
    )
    return {
        "merchant_id": str(merchant_id or ""),
        "csrf_token": csrf_token,
        "email": email,
        "name": name,
        "business_name": business_name or "",
        "picture": picture or "",
        "phone_number": phone_number or "",
        "needs_onboarding": needs_onboarding
    }


@router.post("/onboard")
def onboard(payload: OnboardRequest, request: Request):
    _origin(request)
    user_email = request.session.get("user_email")
    if not user_email:
        raise HTTPException(401, "Authentication required. Please sign in with Google first.")

    # Verify CSRF token
    csrf_hdr = request.headers.get("x-csrf-token")
    if not _same(csrf_hdr, request.session.get("csrf_token")):
        raise HTTPException(403, "Invalid CSRF token")

    clean_name = (payload.name or "").strip() or request.session.get("user_name", "Merchant")
    clean_biz = (payload.business_name or "").strip()
    if not clean_biz:
        raise HTTPException(400, "Business name is required.")

    try:
        clean_phone = normalize_phone(payload.phone_number)
    except ValueError as e:
        raise HTTPException(400, str(e))

    merchant_id = request.session.get("merchant_id")
    picture = request.session.get("picture", "")

    if settings.DATABASE_BACKEND == "postgres" and settings.DATABASE_URL:
        from app.services.postgres_db import PostgresDatabase
        db = PostgresDatabase()
        try:
            with psycopg.connect(db.dsn, connect_timeout=5) as conn:
                # 1. Check if phone is already registered with another email
                conflict = conn.execute(
                    "SELECT id, owner_email FROM merchants WHERE phone_e164 = %s AND (owner_email IS NULL OR owner_email != %s) LIMIT 1",
                    (clean_phone, user_email)
                ).fetchone()
                if conflict:
                    raise HTTPException(400, "This mobile number is already registered to another merchant account.")

                # 2. Check if a merchant record exists for this email
                existing = conn.execute(
                    "SELECT id, tax_address FROM merchants WHERE owner_email = %s LIMIT 1",
                    (user_email,)
                ).fetchone()

                tax_data = existing[1] if existing and isinstance(existing[1], dict) else {}
                tax_data["picture"] = picture or tax_data.get("picture", "")
                tax_data["onboarded"] = True
                tax_json = json.dumps(tax_data)

                if existing:
                    merchant_id = str(existing[0])
                    conn.execute(
                        """UPDATE merchants
                           SET business_name = %s, owner_name = %s, phone_e164 = %s, tax_address = %s
                           WHERE id = %s""",
                        (clean_biz, clean_name, clean_phone, tax_json, merchant_id)
                    )
                else:
                    new_id = merchant_id if (merchant_id and len(merchant_id) == 36) else str(uuid4())
                    conn.execute(
                        """INSERT INTO merchants (id, phone_e164, is_approved, business_name, owner_name, owner_email, tax_address)
                           VALUES (%s, %s, true, %s, %s, %s, %s)""",
                        (new_id, clean_phone, clean_biz, clean_name, user_email, tax_json)
                    )
                    merchant_id = new_id
                conn.commit()
        except HTTPException:
            raise
        except Exception as e:
            logging.error("Failed to persist merchant onboarding in PostgreSQL: %s", e)
            if not merchant_id:
                merchant_id = str(uuid4())
    else:
        if not merchant_id or len(merchant_id) != 36:
            merchant_id = str(uuid4())

    request.session.update(
        merchant_id=str(merchant_id),
        user_name=clean_name,
        business_name=clean_biz,
        phone_number=clean_phone,
        needs_onboarding=False
    )

    return {
        "status": "success",
        "merchant_id": str(merchant_id),
        "name": clean_name,
        "email": user_email,
        "business_name": clean_biz,
        "phone_number": clean_phone,
        "picture": picture,
        "needs_onboarding": False,
        "csrf_token": request.session.get("csrf_token")
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
        phone_number="+919876543210",
        needs_onboarding=False,
        csrf_token=csrf_token
    )
    return {
        "merchant_id": request.session["merchant_id"],
        "csrf_token": csrf_token,
        "email": "demo@clandexis.com",
        "name": payload.username,
        "business_name": "Sri Balaji Textiles",
        "phone_number": "+919876543210",
        "needs_onboarding": False
    }


@router.get("/session")
def session(request: Request):
    user_email = request.session.get("user_email")
    merchant_id = request.session.get("merchant_id")
    if not user_email and not merchant_id:
        raise HTTPException(401, "Authentication required")
    return {
        "merchant_id": merchant_id or "",
        "csrf_token": request.session.get("csrf_token"),
        "email": user_email or "",
        "name": request.session.get("user_name") or "",
        "business_name": request.session.get("business_name") or "",
        "picture": request.session.get("picture") or "",
        "phone_number": request.session.get("phone_number") or "",
        "needs_onboarding": bool(request.session.get("needs_onboarding", False))
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
