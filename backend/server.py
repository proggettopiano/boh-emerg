"""Scorelib backend - FastAPI."""
import os
import io
import re
import uuid
import base64
import hashlib
import logging
import secrets
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, UploadFile, File, Form, Query
from fastapi.responses import Response, FileResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr, Field
import httpx

from auth_utils import (
    hash_password, verify_password, create_jwt, decode_jwt,
    rate_limiter, get_client_ip, get_current_user_id, get_optional_user_id,
)
from pdf_processor import extract_pages, compress_pdf, make_snippet
from email_service import send_password_reset_email
import google_integration as gi

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

UPLOAD_DIR = ROOT_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

ADMIN_LOG_PASSWORD = os.environ.get("ADMIN_LOG_PASSWORD", "Rome02009")  # fallback for dev
APP_NAME = os.environ.get("APP_NAME", "ScoreLib")

app = FastAPI(title=f"{APP_NAME} API")

DEFAULT_CORS_ORIGINS = "https://boh-emerg-wzsa.vercel.app,http://localhost:3000"
allowed_origins = [
    origin.strip()
    for origin in os.environ.get("BACKEND_CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Check ADMIN_LOG_PASSWORD and warn if not set
if not os.environ.get("ADMIN_LOG_PASSWORD"):
    logger.warning("⚠️ ADMIN_LOG_PASSWORD not set - using default fallback 'Rome02009' (dev mode). Set ADMIN_LOG_PASSWORD env var for production.")
ADMIN_LOG_PASSWORD_CONFIGURED = bool(os.environ.get("ADMIN_LOG_PASSWORD"))


# ----------------- Helpers -----------------
async def log_event(event_type: str, description: str, user_id: Optional[str] = None, level: str = "info", meta: Optional[dict] = None):
    doc = {
        "id": str(uuid.uuid4()),
        "event_type": event_type,
        "description": description,
        "user_id": user_id,
        "level": level,
        "meta": meta or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await db.app_logs.insert_one(doc)
    except Exception as e:
        logger.error(f"Failed to write log: {e}")


async def ensure_indexes():
    await db.users.create_index("email", unique=True)
    await db.users.create_index("user_id", unique=True)
    await db.pdfs.create_index([("owner_id", 1), ("created_at", -1)])
    await db.pdfs.create_index("content_hash")
    await db.pdf_pages.create_index([("pdf_id", 1), ("page", 1)])
    try:
        await db.pdf_pages.create_index([("text", "text")], default_language="none")
    except Exception:
        pass
    await db.shared_libraries.create_index("share_token", unique=True)
    await db.password_resets.create_index("token", unique=True)
    await db.password_resets.create_index("expires_at", expireAfterSeconds=0)
    await db.app_logs.create_index([("created_at", -1)])
    await db.system_settings.create_index("key", unique=True)


async def get_master_drive() -> Optional[dict]:
    """Return master drive settings doc {refresh_token, email, folder_root_id} or None."""
    doc = await db.system_settings.find_one({"key": "master_drive"}, {"_id": 0})
    if not doc:
        return None
    return doc.get("value") or None


async def set_master_drive(value: Optional[dict]):
    if value is None:
        await db.system_settings.delete_one({"key": "master_drive"})
        return
    await db.system_settings.update_one({"key": "master_drive"}, {"$set": {"key": "master_drive", "value": value}}, upsert=True)


async def resolve_backup_credentials(user: dict) -> Optional[dict]:
    """Decide where backups go. Returns dict {refresh_token, owner: 'master'|'user', folder_root_id} or None."""
    master = await get_master_drive()
    if master and master.get("refresh_token"):
        return {"refresh_token": master["refresh_token"], "owner": "master", "folder_root_id": master.get("folder_root_id"), "email": master.get("email")}
    if user.get("google_refresh_token"):
        return {"refresh_token": user["google_refresh_token"], "owner": "user", "folder_root_id": user.get("drive_folder_id")}
    return None


async def seed_admin():
    email = "admin@scorelib.app"
    existing = await db.users.find_one({"email": email}, {"_id": 0, "user_id": 1})
    if existing:
        return
    user_id = f"user_admin_{uuid.uuid4().hex[:8]}"
    await db.users.insert_one({
        "user_id": user_id,
        "email": email,
        "password_hash": hash_password("Admin02009!"),
        "name": "Admin",
        "picture": "",
        "how_found": "seed",
        "backup_enabled": True,
        "profile_completed": True,
        "auth_provider": "password",
        "is_admin": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    logger.info(f"Seeded admin user: {email}")


async def migrate_storage_fields():
    """Backfill storage_type / file_path on existing PDFs."""
    n = 0
    async for p in db.pdfs.find({"$or": [{"storage_type": {"$exists": False}}, {"file_path": {"$exists": False}}]}, {"_id": 0}):
        st = "google_drive" if p.get("drive_file_id") else "local"
        fpath = UPLOAD_DIR / p["owner_id"] / f"{p['id']}.pdf"
        await db.pdfs.update_one({"id": p["id"]}, {"$set": {
            "storage_type": st,
            "file_path": str(fpath.resolve()),
        }})
        n += 1
    if n:
        logger.info(f"Migrated storage fields on {n} PDFs")


async def migrate_email_verified():
    """Backfill email_verified: true for existing users without the field."""
    n = 0
    async for u in db.users.find({"email_verified": {"$exists": False}}, {"_id": 0, "user_id": 1}):
        await db.users.update_one({"user_id": u["user_id"]}, {"$set": {"email_verified": True}})
        n += 1
    if n:
        logger.info(f"Migrated email_verified on {n} users")


@app.on_event("startup")
async def on_start():
    await ensure_indexes()
    await seed_admin()
    await migrate_storage_fields()
    await migrate_email_verified()
    logger.info("Startup complete")


# ----------------- Models -----------------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ForgotIn(BaseModel):
    email: EmailStr


class ResendVerificationIn(BaseModel):
    email: EmailStr


class VerifyEmailIn(BaseModel):
    token: str


class ResetIn(BaseModel):
    token: str
    password: str = Field(min_length=6)


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    picture: Optional[str] = None  # base64 data URL
    how_found: Optional[str] = None
    profile_completed: Optional[bool] = None


class ChangeEmailIn(BaseModel):
    password: str
    new_email: EmailStr


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


class DeleteAccountIn(BaseModel):
    password: Optional[str] = None


class BackupToggleIn(BaseModel):
    enabled: bool


class CreateLibraryIn(BaseModel):
    name: str
    description: Optional[str] = ""


class AddPdfsIn(BaseModel):
    pdf_ids: List[str]


class GoogleAuthIn(BaseModel):
    code: str
    redirect_uri: str


class GoogleAuthUrlIn(BaseModel):
    redirect_uri: str


class AdminPwdIn(BaseModel):
    password: str


class PdfPatchIn(BaseModel):
    title: Optional[str] = None
    is_favorite: Optional[bool] = None
    tags: Optional[List[str]] = None


# ----------------- Auth -----------------
def user_public(u: dict) -> dict:
    return {
        "user_id": u["user_id"],
        "email": u["email"],
        "name": u.get("name", ""),
        "picture": u.get("picture", ""),
        "how_found": u.get("how_found", ""),
        "backup_enabled": u.get("backup_enabled", False),
        "profile_completed": u.get("profile_completed", False),
        "auth_provider": u.get("auth_provider", "password"),
        "has_google_drive": bool(u.get("google_refresh_token")),
        "is_admin": u.get("is_admin", False),
        "email_verified": u.get("email_verified", True),  # default true for backward compatibility
        "created_at": u.get("created_at"),
    }


@api.post("/auth/register")
async def register(payload: RegisterIn, request: Request):
    ip = get_client_ip(request)
    ok, retry = rate_limiter.check("register", ip, max_attempts=5, window_sec=3600, block_sec=3600)
    if not ok:
        await log_event("auth.register.blocked", f"Rate limit reached for IP {ip}", level="warn", meta={"ip": ip})
        raise HTTPException(status_code=429, detail=f"Troppi tentativi. Riprova tra {retry}s.")

    email = payload.email.lower().strip()
    existing = await db.users.find_one({"email": email}, {"_id": 0, "user_id": 1})
    if existing:
        await log_event("auth.register.duplicate", f"Tentativo registrazione email esistente: {email}", level="warn", meta={"ip": ip})
        raise HTTPException(status_code=409, detail="Email già registrata. Usa il recupero password.")

    user_id = f"user_{uuid.uuid4().hex[:12]}"
    # Generate verification token
    verification_token = secrets.token_urlsafe(32)
    verification_token_hash = hashlib.sha256(verification_token.encode()).hexdigest()
    verification_expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    doc = {
        "user_id": user_id,
        "email": email,
        "password_hash": hash_password(payload.password),
        "name": "",
        "picture": "",
        "how_found": "",
        "backup_enabled": False,
        "profile_completed": False,
        "auth_provider": "password",
        "email_verified": False,
        "verification_token_hash": verification_token_hash,
        "verification_expires_at": verification_expires_at.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(doc)
    await log_event("auth.register", f"Nuovo account creato: {email}", user_id=user_id)

    # Try to send verification email
    frontend_url = request.headers.get("origin") or request.headers.get("referer", "").rstrip("/")
    if not frontend_url:
        frontend_url = ""
    verification_link = f"{frontend_url}/verify-email?token={verification_token}"
    from email_service import send_verification_email
    email_sent = await send_verification_email(email, verification_link, "")
    if email_sent:
        await log_event("verification_email_sent", f"Email verifica inviata a {email}", user_id=user_id)
        return {"status": "verification_email_sent", "message": "Account creato. Controlla la tua email per verificare l'account."}
    else:
        await log_event("verification_email_failed", f"Invio email verifica fallito per {email}", user_id=user_id, level="error")
        return {"status": "verification_pending", "message": "Account creato. Non siamo riusciti a inviare l'email di verifica. Usa 'Reinvia email'."}


@api.post("/auth/login")
async def login(payload: LoginIn, request: Request):
    ip = get_client_ip(request)
    ok, retry = rate_limiter.check("login", ip, max_attempts=10, window_sec=900, block_sec=900)
    if not ok:
        raise HTTPException(status_code=429, detail=f"Troppi tentativi. Riprova tra {retry}s.")

    email = payload.email.lower().strip()
    u = await db.users.find_one({"email": email}, {"_id": 0})
    if not u or not u.get("password_hash") or not verify_password(payload.password, u["password_hash"]):
        await log_event("auth.login.fail", f"Login fallito per {email}", level="warn", meta={"ip": ip})
        raise HTTPException(status_code=401, detail="Email o password errati")
    if u.get("email_verified") is False:
        await log_event("auth.login.unverified", f"Tentativo login email non verificata: {email}", user_id=u["user_id"], level="warn")
        raise HTTPException(status_code=403, detail="Verifica prima la tua email per accedere.")
    token = create_jwt(u["user_id"])
    await log_event("auth.login", f"Login: {email}", user_id=u["user_id"])
    return {"token": token, "user": user_public(u)}


@api.post("/auth/resend-verification")
async def resend_verification(payload: ResendVerificationIn, request: Request):
    email = payload.email.lower().strip()
    u = await db.users.find_one({"email": email}, {"_id": 0})
    if not u:
        # Don't reveal if email exists
        return {"ok": True, "message": "Se l'email esiste, riceverai un link di verifica."}
    if u.get("email_verified"):
        return {"ok": True, "message": "Email già verificata."}

    # Generate new token
    verification_token = secrets.token_urlsafe(32)
    verification_token_hash = hashlib.sha256(verification_token.encode()).hexdigest()
    verification_expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    await db.users.update_one({"user_id": u["user_id"]}, {"$set": {
        "verification_token_hash": verification_token_hash,
        "verification_expires_at": verification_expires_at.isoformat(),
    }})

    frontend_url = request.headers.get("origin") or request.headers.get("referer", "").rstrip("/")
    if not frontend_url:
        frontend_url = ""
    verification_link = f"{frontend_url}/verify-email?token={verification_token}"
    from email_service import send_verification_email
    email_sent = await send_verification_email(email, verification_link, u.get("name", ""))
    if email_sent:
        await log_event("verification_email_sent", f"Email verifica reinviata a {email}", user_id=u["user_id"])
        return {"ok": True, "message": "Email di verifica reinviata."}
    else:
        await log_event("verification_email_failed", f"Reinvio email verifica fallito per {email}", user_id=u["user_id"], level="error")
        return {"ok": False, "message": "Errore nell'invio dell'email. Riprova più tardi."}


@api.get("/auth/verify-email")
async def verify_email(token: str = Query(...)):
    if not token:
        raise HTTPException(status_code=400, detail="Token mancante")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    u = await db.users.find_one({"verification_token_hash": token_hash}, {"_id": 0})
    if not u:
        await log_event("email_verification_failed", f"Token verifica non trovato: {token[:10]}...", level="warn")
        raise HTTPException(status_code=400, detail="Link non valido o già usato")
    exp_str = u.get("verification_expires_at")
    if not exp_str:
        raise HTTPException(status_code=400, detail="Token scaduto")
    exp = datetime.fromisoformat(exp_str)
    if datetime.now(timezone.utc) > exp:
        await log_event("email_verification_expired", f"Token verifica scaduto per {u['email']}", user_id=u["user_id"], level="warn")
        raise HTTPException(status_code=400, detail="Link scaduto. Richiedi un nuovo link di verifica.")
    # Verify
    await db.users.update_one({"user_id": u["user_id"]}, {"$set": {"email_verified": True}, "$unset": {"verification_token_hash": "", "verification_expires_at": ""}})
    jwt_token = create_jwt(u["user_id"])
    await log_event("email_verified", f"Email verificata per {u['email']}", user_id=u["user_id"])
    return {"token": jwt_token, "user": user_public({**u, "email_verified": True})}


@api.post("/auth/forgot")
async def forgot(payload: ForgotIn, request: Request):
    ip = get_client_ip(request)
    ok, _ = rate_limiter.check("forgot", ip, max_attempts=5, window_sec=3600, block_sec=3600)
    if not ok:
        raise HTTPException(status_code=429, detail="Troppi tentativi. Riprova più tardi.")
    email = payload.email.lower().strip()
    u = await db.users.find_one({"email": email}, {"_id": 0})
    # always return ok, but send only if exists
    if u and u.get("auth_provider") == "password":
        token = secrets.token_urlsafe(32)
        await db.password_resets.insert_one({
            "token": token,
            "user_id": u["user_id"],
            "email": email,
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=60),
            "created_at": datetime.now(timezone.utc),
        })
        frontend_url = request.headers.get("origin") or request.headers.get("referer", "").rstrip("/")
        if not frontend_url:
            frontend_url = ""
        reset_link = f"{frontend_url}/reset?token={token}"
        await send_password_reset_email(email, reset_link, u.get("name", ""))
        await log_event("auth.forgot", f"Reset password richiesto per {email}", user_id=u["user_id"])
    return {"ok": True, "message": "Se l'email esiste, riceverai un link per il reset."}


@api.post("/auth/reset")
async def reset_password(payload: ResetIn):
    rec = await db.password_resets.find_one({"token": payload.token}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=400, detail="Link non valido o scaduto")
    exp = rec["expires_at"]
    if isinstance(exp, str):
        exp = datetime.fromisoformat(exp)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Link scaduto")
    new_hash = hash_password(payload.password)
    await db.users.update_one({"user_id": rec["user_id"]}, {"$set": {"password_hash": new_hash}})
    await db.password_resets.delete_one({"token": payload.token})
    await log_event("auth.reset", f"Password reimpostata per {rec['email']}", user_id=rec["user_id"])
    token = create_jwt(rec["user_id"])
    u = await db.users.find_one({"user_id": rec["user_id"]}, {"_id": 0})
    return {"token": token, "user": user_public(u)}


@api.post("/auth/google/url")
async def google_auth_url(payload: GoogleAuthUrlIn):
    if not gi.google_configured():
        raise HTTPException(status_code=503, detail="Google OAuth non configurato")
    state = secrets.token_urlsafe(16)
    url = gi.build_auth_url(payload.redirect_uri, state)
    return {"url": url, "state": state}


@api.post("/auth/google")
async def google_auth(payload: GoogleAuthIn):
    """Exchange authorization code for our JWT and store refresh token for Drive."""
    if not gi.google_configured():
        raise HTTPException(status_code=503, detail="Google OAuth non configurato")
    try:
        tokens = await gi.exchange_code(payload.code, payload.redirect_uri)
    except Exception as e:
        logger.error(f"Google token exchange error: {e}")
        await log_event("auth.google.fail", f"Code exchange failed: {e}", level="error")
        raise HTTPException(status_code=401, detail="Codice Google non valido")
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    try:
        info = await gi.fetch_userinfo(access_token)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"userinfo failed: {e}")
    email = (info.get("email") or "").lower().strip()
    name = info.get("name", "")
    picture = info.get("picture", "")
    if not email:
        raise HTTPException(status_code=400, detail="Email mancante da Google")

    u = await db.users.find_one({"email": email}, {"_id": 0})
    if not u:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        u = {
            "user_id": user_id,
            "email": email,
            "name": name,
            "picture": picture,
            "how_found": "",
            "backup_enabled": False,
            "profile_completed": False,
            "auth_provider": "google",
            "google_refresh_token": refresh_token or "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.users.insert_one(u)
        await log_event("auth.google.new", f"Nuovo utente Google: {email}", user_id=user_id)
    else:
        upd = {
            "auth_provider": u.get("auth_provider", "google"),
            "picture": picture or u.get("picture", ""),
            "name": u.get("name") or name,
        }
        if refresh_token:
            upd["google_refresh_token"] = refresh_token
        await db.users.update_one({"user_id": u["user_id"]}, {"$set": upd})
        await log_event("auth.google.login", f"Login Google: {email}", user_id=u["user_id"])
        u = await db.users.find_one({"user_id": u["user_id"]}, {"_id": 0})

    token = create_jwt(u["user_id"])
    return {"token": token, "user": user_public(u)}


@api.get("/auth/me")
async def me(user_id: str = Depends(get_current_user_id)):
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    return user_public(u)


# ----------------- Profile / Settings -----------------
@api.patch("/profile")
async def update_profile(payload: ProfileUpdate, user_id: str = Depends(get_current_user_id)):
    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if update:
        await db.users.update_one({"user_id": user_id}, {"$set": update})
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    return user_public(u)


@api.post("/settings/email")
async def change_email(payload: ChangeEmailIn, user_id: str = Depends(get_current_user_id)):
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    if u.get("auth_provider") == "password":
        if not u.get("password_hash") or not verify_password(payload.password, u["password_hash"]):
            raise HTTPException(status_code=401, detail="Password errata")
    new_email = payload.new_email.lower().strip()
    other = await db.users.find_one({"email": new_email}, {"_id": 0, "user_id": 1})
    if other and other["user_id"] != user_id:
        raise HTTPException(status_code=409, detail="Email già in uso")
    await db.users.update_one({"user_id": user_id}, {"$set": {"email": new_email}})
    await log_event("settings.email", f"Email cambiata a {new_email}", user_id=user_id)
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    return user_public(u)


@api.post("/settings/password")
async def change_password(payload: ChangePasswordIn, user_id: str = Depends(get_current_user_id)):
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    if u.get("auth_provider") == "password":
        if not verify_password(payload.current_password, u.get("password_hash", "")):
            raise HTTPException(status_code=401, detail="Password attuale errata")
    new_hash = hash_password(payload.new_password)
    await db.users.update_one({"user_id": user_id}, {"$set": {"password_hash": new_hash, "auth_provider": "password"}})
    await log_event("settings.password", "Password cambiata", user_id=user_id)
    return {"ok": True}


@api.post("/auth/google/connect")
async def google_connect(payload: GoogleAuthIn, user_id: str = Depends(get_current_user_id)):
    """Connect Google Drive to an existing logged-in account."""
    if not gi.google_configured():
        raise HTTPException(status_code=503, detail="Google OAuth non configurato")
    try:
        tokens = await gi.exchange_code(payload.code, payload.redirect_uri)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Codice Google non valido: {e}")
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Nessun refresh token ricevuto. Riprova autorizzando l'app.")
    info = await gi.fetch_userinfo(tokens.get("access_token"))
    email = (info.get("email") or "").lower().strip()
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    if u["email"].lower() != email:
        raise HTTPException(status_code=400, detail=f"L'account Google ({email}) non corrisponde all'email del profilo ({u['email']})")
    await db.users.update_one({"user_id": user_id}, {"$set": {"google_refresh_token": refresh_token, "google_email": email}})
    await log_event("drive.connect", f"Drive connesso per {email}", user_id=user_id)
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    return user_public(u)


@api.post("/backup/run")
async def backup_run(user_id: str = Depends(get_current_user_id)):
    """Backup all user PDFs missing a drive_file_id to Drive. Requires connected Drive."""
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not u or not u.get("google_refresh_token"):
        raise HTTPException(status_code=400, detail="Drive non connesso. Connetti Google Drive prima.")
    refresh = u["google_refresh_token"]
    folder_id = u.get("drive_folder_id")
    try:
        if not folder_id:
            folder_id = await asyncio.to_thread(gi.ensure_user_folder, refresh, user_id)
            await db.users.update_one({"user_id": user_id}, {"$set": {"drive_folder_id": folder_id}})
    except Exception as e:
        await log_event("backup.drive.error", f"Folder error: {e}", user_id=user_id, level="error")
        raise HTTPException(status_code=502, detail=f"Errore Drive: {e}")
    pending = await db.pdfs.find({"owner_id": user_id, "drive_file_id": {"$in": [None, ""]}}, {"_id": 0}).to_list(10000)
    uploaded = 0
    errors = 0
    for p in pending:
        fpath = UPLOAD_DIR / user_id / f"{p['id']}.pdf"
        if not fpath.exists():
            continue
        try:
            data = fpath.read_bytes()
            drive_id = await asyncio.to_thread(gi.upload_to_drive, refresh, folder_id, f"{p['id']}.pdf", data)
            await db.pdfs.update_one({"id": p["id"]}, {"$set": {"drive_file_id": drive_id}})
            uploaded += 1
        except Exception as e:
            errors += 1
            await log_event("backup.drive.error", f"Upload {p.get('title')}: {e}", user_id=user_id, level="error")
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.users.update_one({"user_id": user_id}, {"$set": {"last_backup_at": now_iso}})
    await log_event("backup.run", f"Backup completato: {uploaded} file caricati, {errors} errori", user_id=user_id)
    return {"ok": True, "uploaded": uploaded, "errors": errors, "last_backup_at": now_iso}


@api.get("/backup/status")
async def backup_status(user_id: str = Depends(get_current_user_id)):
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    total = await db.pdfs.count_documents({"owner_id": user_id})
    backed = await db.pdfs.count_documents({"owner_id": user_id, "drive_file_id": {"$nin": [None, ""]}})
    return {
        "backup_enabled": u.get("backup_enabled", False),
        "drive_connected": bool(u.get("google_refresh_token")),
        "drive_email": u.get("google_email", ""),
        "drive_folder_id": u.get("drive_folder_id"),
        "last_backup_at": u.get("last_backup_at"),
        "total_pdfs": total,
        "backed_up_pdfs": backed,
        "pending_pdfs": max(0, total - backed),
    }


@api.post("/backup/test")
async def backup_test(user_id: str = Depends(get_current_user_id)):
    """Admin-style smoke test: uploads a tiny test file, lists folder, deletes the test file."""
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not u or not u.get("google_refresh_token"):
        raise HTTPException(status_code=400, detail="Drive non connesso")
    if not u.get("is_admin"):
        raise HTTPException(status_code=403, detail="Solo admin")
    refresh = u["google_refresh_token"]
    try:
        folder_id = u.get("drive_folder_id") or await asyncio.to_thread(gi.ensure_user_folder, refresh, user_id)
        if folder_id != u.get("drive_folder_id"):
            await db.users.update_one({"user_id": user_id}, {"$set": {"drive_folder_id": folder_id}})
        test_data = b"%PDF-1.4\n%scorelib backup test\n1 0 obj<<>>endobj\ntrailer<</Size 1>>\n%%EOF\n"
        drive_id = await asyncio.to_thread(gi.upload_to_drive, refresh, folder_id, f"_scorelib_test_{uuid.uuid4().hex[:6]}.pdf", test_data)
        files = await asyncio.to_thread(gi.list_drive_files, refresh, folder_id)
        await asyncio.to_thread(gi.delete_from_drive, refresh, drive_id)
        await log_event("backup.test", f"Test backup OK · folder={folder_id} · {len(files)} file(s)", user_id=user_id)
        return {"ok": True, "folder_id": folder_id, "files_count": len(files), "test_file_id": drive_id}
    except Exception as e:
        await log_event("backup.test.fail", f"Test backup fallito: {e}", user_id=user_id, level="error")
        raise HTTPException(status_code=502, detail=f"Test fallito: {e}")


@api.post("/settings/backup")
async def set_backup(payload: BackupToggleIn, user_id: str = Depends(get_current_user_id)):
    if payload.enabled:
        u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        creds = await resolve_backup_credentials(u or {})
        if not creds:
            raise HTTPException(status_code=400, detail="Per attivare il backup, l'admin deve connettere il Master Drive oppure tu devi connettere il tuo Google Drive.")
    await db.users.update_one({"user_id": user_id}, {"$set": {"backup_enabled": payload.enabled}})
    await log_event("settings.backup", f"Backup {'attivato' if payload.enabled else 'disattivato'}", user_id=user_id)
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    return user_public(u)


@api.delete("/settings/account")
async def delete_account(user_id: str = Depends(get_current_user_id)):
    # delete pdfs files
    pdfs = await db.pdfs.find({"owner_id": user_id}, {"_id": 0, "id": 1}).to_list(10000)
    for p in pdfs:
        fpath = UPLOAD_DIR / user_id / f"{p['id']}.pdf"
        if fpath.exists():
            try:
                fpath.unlink()
            except Exception:
                pass
    await db.pdfs.delete_many({"owner_id": user_id})
    await db.pdf_pages.delete_many({"owner_id": user_id})
    await db.shared_libraries.delete_many({"owner_id": user_id})
    await db.users.delete_one({"user_id": user_id})
    await log_event("settings.account.delete", "Account cancellato", user_id=user_id, level="warn")
    return {"ok": True}


# ----------------- PDFs -----------------
@api.post("/pdfs/upload")
async def upload_pdfs(
    files: List[UploadFile] = File(...),
    user_id: str = Depends(get_current_user_id),
):
    results = []
    user_dir = UPLOAD_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        try:
            data = await f.read()
            if not data[:4] == b"%PDF":
                results.append({"name": f.filename, "ok": False, "error": "Non è un PDF valido"})
                await log_event("pdf.invalid", f"File non valido: {f.filename}", user_id=user_id, level="warn")
                continue
            original_size = len(data)
            # hash on ORIGINAL bytes (deterministic)
            content_hash = hashlib.sha256(data).hexdigest()
            # duplicate detector for this user
            dup = await db.pdfs.find_one({"owner_id": user_id, "content_hash": content_hash}, {"_id": 0, "id": 1, "title": 1})
            if dup:
                results.append({"name": f.filename, "ok": False, "duplicate": True, "existing_id": dup["id"], "existing_title": dup.get("title", ""), "error": "Questo PDF esiste già nella tua libreria"})
                await log_event("pdf.duplicate", f"Duplicato rilevato: {f.filename}", user_id=user_id, level="warn")
                continue
            compressed_data, was_compressed = compress_pdf(data)
            data = compressed_data
            # extract text + OCR
            try:
                pages_text, total_pages, used_ocr = extract_pages(data)
            except Exception as e:
                results.append({"name": f.filename, "ok": False, "error": "PDF non leggibile"})
                await log_event(
                    "pdf.error",
                    f"Errore estrazione testo da {f.filename}: {e}",
                    user_id=user_id, level="error",
                    meta={"filename": f.filename, "error": str(e), "stage": "extract"},
                )
                continue
            pdf_id = str(uuid.uuid4())
            fpath = user_dir / f"{pdf_id}.pdf"
            with open(fpath, "wb") as out:
                out.write(data)
            file_path_str = str(fpath.resolve())
            await log_event(
                "pdf.save",
                f"File scritto su disco: {file_path_str} ({len(data)} bytes)",
                user_id=user_id,
                meta={"pdf_id": pdf_id, "filename": f.filename, "path": file_path_str, "size": len(data)},
            )
            title = (f.filename or "untitled.pdf").rsplit(".", 1)[0]
            doc = {
                "id": pdf_id,
                "owner_id": user_id,
                "title": title,
                "filename": f.filename,
                "size": len(data),
                "original_size": original_size,
                "compressed": was_compressed,
                "pages": total_pages,
                "used_ocr": used_ocr,
                "content_hash": content_hash,
                "drive_file_id": None,
                "storage_type": "local",
                "file_path": file_path_str,
                "synced_at": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.pdfs.insert_one(doc)
            page_docs = [{
                "pdf_id": pdf_id,
                "owner_id": user_id,
                "page": i + 1,
                "text": (t or "")[:50000],
            } for i, t in enumerate(pages_text)]
            if page_docs:
                await db.pdf_pages.insert_many(page_docs)
            # Drive backup if enabled
            user_doc = await db.users.find_one({"user_id": user_id}, {"_id": 0})
            drive_uploaded = False
            drive_id = None
            drive_owner = None
            if user_doc and user_doc.get("backup_enabled"):
                creds = await resolve_backup_credentials(user_doc)
                if creds:
                    try:
                        refresh = creds["refresh_token"]
                        # ensure folder for this user_id under either master or user's root
                        if creds["owner"] == "master":
                            # master drive: cache root folder id in system_settings; subfolder per user
                            master = await get_master_drive() or {}
                            master_root = master.get("folder_root_id")
                            if not master_root:
                                master_root = await asyncio.to_thread(gi.ensure_master_root, refresh)
                                master["folder_root_id"] = master_root
                                master["refresh_token"] = refresh
                                await set_master_drive(master)
                            user_folder = await asyncio.to_thread(gi.ensure_subfolder, refresh, master_root, user_id)
                        else:
                            user_folder = creds.get("folder_root_id")
                            if not user_folder:
                                user_folder = await asyncio.to_thread(gi.ensure_user_folder, refresh, user_id)
                                await db.users.update_one({"user_id": user_id}, {"$set": {"drive_folder_id": user_folder}})
                        drive_id = await asyncio.to_thread(gi.upload_to_drive, refresh, user_folder, f"{pdf_id}.pdf", data)
                        drive_owner = creds["owner"]
                        sync_iso = datetime.now(timezone.utc).isoformat()
                        await db.pdfs.update_one({"id": pdf_id}, {"$set": {
                            "drive_file_id": drive_id,
                            "drive_owner": drive_owner,
                            "storage_type": "google_drive",
                            "synced_at": sync_iso,
                        }})
                        drive_uploaded = True
                        await log_event(
                            "pdf.sync",
                            f"Sincronizzato su Google Drive ({drive_owner.upper()}) · fileId={drive_id} · folder={user_folder}",
                            user_id=user_id,
                            meta={"pdf_id": pdf_id, "drive_file_id": drive_id, "folder_id": user_folder, "drive_owner": drive_owner, "filename": f.filename},
                        )
                    except Exception as e:
                        logger.error(f"Drive backup failed: {e}")
                        await log_event(
                            "pdf.error",
                            f"Sync Drive fallito per {f.filename}: {e}",
                            user_id=user_id, level="error",
                            meta={"pdf_id": pdf_id, "filename": f.filename, "error": str(e), "stage": "drive_sync"},
                        )
            # storage decision log
            if drive_uploaded:
                await log_event(
                    "pdf.storage",
                    f"Storage finale: GOOGLE_DRIVE · driveFileId={drive_id} · localCache={file_path_str}",
                    user_id=user_id,
                    meta={"pdf_id": pdf_id, "storage_type": "google_drive", "drive_file_id": drive_id, "file_path": file_path_str},
                )
            else:
                await log_event(
                    "pdf.storage",
                    f"Storage finale: LOCAL · path={file_path_str}",
                    user_id=user_id,
                    meta={"pdf_id": pdf_id, "storage_type": "local", "file_path": file_path_str},
                )
            await log_event(
                "pdf.upload",
                f"Caricato: {f.filename} ({total_pages}pp{', OCR' if used_ocr else ''}{', compresso' if was_compressed else ''}) → {('GOOGLE_DRIVE' if drive_uploaded else 'LOCAL')}",
                user_id=user_id,
                meta={
                    "pdf_id": pdf_id, "filename": f.filename, "pages": total_pages,
                    "ocr": used_ocr, "compressed": was_compressed,
                    "storage_type": "google_drive" if drive_uploaded else "local",
                    "drive_file_id": drive_id, "file_path": file_path_str,
                },
            )
            if was_compressed:
                await log_event("pdf.compress", f"PDF compresso: {f.filename} ({original_size}→{len(data)} bytes)", user_id=user_id, meta={"pdf_id": pdf_id})
            if used_ocr:
                await log_event("pdf.ocr", f"OCR eseguito su: {f.filename}", user_id=user_id, meta={"pdf_id": pdf_id})
            results.append({
                "name": f.filename, "ok": True, "pdf_id": pdf_id, "pages": total_pages,
                "ocr": used_ocr, "compressed": was_compressed, "drive": drive_uploaded,
                "storage_type": "google_drive" if drive_uploaded else "local",
                "file_path": file_path_str, "drive_file_id": drive_id,
            })
        except Exception as e:
            logger.exception("upload failed")
            results.append({"name": f.filename, "ok": False, "error": str(e)})
            await log_event("pdf.error", f"Errore upload {f.filename}: {e}", user_id=user_id, level="error")
    return {"results": results}


def _serialize_pdf(p: dict) -> dict:
    return {
        "id": p["id"],
        "title": p.get("title", ""),
        "filename": p.get("filename", ""),
        "size": p.get("size", 0),
        "pages": p.get("pages", 0),
        "used_ocr": p.get("used_ocr", False),
        "compressed": p.get("compressed", False),
        "is_favorite": p.get("is_favorite", False),
        "tags": p.get("tags", []),
        "storage_type": p.get("storage_type", "local"),
        "file_path": p.get("file_path", ""),
        "drive_file_id": p.get("drive_file_id"),
        "synced_at": p.get("synced_at"),
        "created_at": p.get("created_at"),
    }


@api.get("/pdfs")
async def list_pdfs(
    sort: str = Query("date_desc"),
    favorite: Optional[bool] = None,
    tag: Optional[str] = None,
    user_id: str = Depends(get_current_user_id),
):
    sort_map = {
        "date_desc": [("created_at", -1)],
        "date_asc": [("created_at", 1)],
        "name_asc": [("title", 1)],
        "name_desc": [("title", -1)],
    }
    flt: Dict[str, Any] = {"owner_id": user_id}
    if favorite is True:
        flt["is_favorite"] = True
    if tag:
        flt["tags"] = tag
    cursor = db.pdfs.find(flt, {"_id": 0}).sort(sort_map.get(sort, [("created_at", -1)]))
    items = await cursor.to_list(10000)
    all_tags = await db.pdfs.distinct("tags", {"owner_id": user_id})
    return {"items": [_serialize_pdf(p) for p in items], "tags": sorted([t for t in all_tags if t])}


@api.patch("/pdfs/{pdf_id}")
async def update_pdf(pdf_id: str, payload: PdfPatchIn, user_id: str = Depends(get_current_user_id)):
    p = await db.pdfs.find_one({"id": pdf_id, "owner_id": user_id}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="PDF non trovato")
    update: Dict[str, Any] = {}
    if payload.title is not None:
        update["title"] = payload.title.strip()[:200]
    if payload.is_favorite is not None:
        update["is_favorite"] = payload.is_favorite
    if payload.tags is not None:
        update["tags"] = sorted(set([t.strip().lower() for t in payload.tags if t and t.strip()]))[:20]
    if update:
        await db.pdfs.update_one({"id": pdf_id}, {"$set": update})
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    return _serialize_pdf(p)


@api.get("/pdfs/{pdf_id}")
async def get_pdf(pdf_id: str, user_id: str = Depends(get_current_user_id)):
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="PDF non trovato")
    if p["owner_id"] != user_id:
        accessible = await _user_can_access_pdf(user_id, pdf_id)
        if not accessible:
            raise HTTPException(status_code=403, detail="Accesso negato")
    await log_event("pdf.open", f"Apertura PDF: {p.get('title')}", user_id=user_id)
    return _serialize_pdf(p)


@api.get("/pdfs/{pdf_id}/file")
async def get_pdf_file(pdf_id: str, user_id: str = Depends(get_current_user_id)):
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="PDF non trovato")
    if p["owner_id"] != user_id:
        accessible = await _user_can_access_pdf(user_id, pdf_id)
        if not accessible:
            raise HTTPException(status_code=403, detail="Accesso negato")
    fpath = UPLOAD_DIR / p["owner_id"] / f"{pdf_id}.pdf"
    if fpath.exists():
        return FileResponse(fpath, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{p.get("filename", pdf_id)}"'})
    # local missing — fall back to Drive (master or user)
    if p.get("drive_file_id"):
        refresh = None
        if p.get("drive_owner") == "master":
            master = await get_master_drive()
            refresh = master.get("refresh_token") if master else None
        else:
            owner = await db.users.find_one({"user_id": p["owner_id"]}, {"_id": 0})
            refresh = (owner or {}).get("google_refresh_token")
        if refresh:
            try:
                data = await asyncio.to_thread(gi.download_from_drive, refresh, p["drive_file_id"])
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_bytes(data)
                await log_event("pdf.sync", f"Restore Drive → cache locale: {p.get('title')}", user_id=p["owner_id"], meta={"pdf_id": pdf_id, "drive_file_id": p["drive_file_id"]})
                return Response(content=data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{p.get("filename", pdf_id)}"'})
            except Exception as e:
                logger.error(f"Drive restore failed: {e}")
                await log_event("pdf.error", f"Restore Drive fallito: {e}", user_id=p["owner_id"], level="error", meta={"pdf_id": pdf_id, "stage": "drive_restore"})
    raise HTTPException(status_code=404, detail="File mancante")


@api.delete("/pdfs/{pdf_id}")
async def delete_pdf(pdf_id: str, user_id: str = Depends(get_current_user_id)):
    p = await db.pdfs.find_one({"id": pdf_id, "owner_id": user_id}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="PDF non trovato")
    fpath = UPLOAD_DIR / user_id / f"{pdf_id}.pdf"
    if fpath.exists():
        try:
            fpath.unlink()
        except Exception:
            pass
    # delete from Drive if exists
    if p.get("drive_file_id"):
        u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        if u and u.get("google_refresh_token"):
            try:
                await asyncio.to_thread(gi.delete_from_drive, u["google_refresh_token"], p["drive_file_id"])
            except Exception as e:
                logger.warning(f"Drive delete failed: {e}")
    await db.pdfs.delete_one({"id": pdf_id})
    await db.pdf_pages.delete_many({"pdf_id": pdf_id})
    await db.shared_libraries.update_many({"owner_id": user_id}, {"$pull": {"pdf_ids": pdf_id}})
    await log_event("pdf.delete", f"Eliminato PDF: {p.get('title')}", user_id=user_id)
    return {"ok": True}


async def _user_can_access_pdf(user_id: str, pdf_id: str) -> bool:
    """User can access a non-owned pdf if it belongs to a shared library they have access to."""
    libs = await db.shared_libraries.find(
        {"pdf_ids": pdf_id, "$or": [{"owner_id": user_id}, {"members": user_id}, {"public": True}]},
        {"_id": 0, "id": 1},
    ).to_list(100)
    return len(libs) > 0


# ----------------- Search -----------------
@api.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    library_id: Optional[str] = None,
    user_id: str = Depends(get_current_user_id),
):
    q = q.strip()
    if len(q) < 1:
        return {"results": []}
    await log_event("search", f"Ricerca: '{q}'" + (f" in libreria {library_id}" if library_id else ""), user_id=user_id)

    # Determine accessible PDF set: owned + shared library memberships
    owned_filter = {"owner_id": user_id}
    accessible_pdf_ids: set = set()
    pdfs_meta: Dict[str, dict] = {}
    pdf_source: Dict[str, str] = {}  # pdf_id -> 'personal' | 'shared:<lib_id>'

    # personal pdfs
    async for p in db.pdfs.find(owned_filter, {"_id": 0}):
        accessible_pdf_ids.add(p["id"])
        pdfs_meta[p["id"]] = p
        pdf_source[p["id"]] = "personal"

    # shared libraries: owner or members
    lib_filter = {"$or": [{"owner_id": user_id}, {"members": user_id}, {"public": True}]}
    if library_id:
        lib_filter = {"id": library_id, **lib_filter}
    async for lib in db.shared_libraries.find(lib_filter, {"_id": 0}):
        for pid in lib.get("pdf_ids", []):
            if pid not in accessible_pdf_ids:
                p = await db.pdfs.find_one({"id": pid}, {"_id": 0})
                if p:
                    accessible_pdf_ids.add(pid)
                    pdfs_meta[pid] = p
                    pdf_source[pid] = f"shared:{lib['name']}"
            elif pdf_source.get(pid) == "personal":
                # already personal -> dedupe priority: personal wins
                pass

    if library_id:
        # restrict to this library only
        lib = await db.shared_libraries.find_one({"id": library_id}, {"_id": 0})
        if not lib:
            raise HTTPException(status_code=404, detail="Libreria non trovata")
        if lib["owner_id"] != user_id and user_id not in lib.get("members", []) and not lib.get("public"):
            raise HTTPException(status_code=403, detail="Accesso negato")
        scope_ids = set(lib.get("pdf_ids", []))
        accessible_pdf_ids &= scope_ids

    if not accessible_pdf_ids:
        return {"results": []}

    safe_q = re.escape(q)

    # Search pages text for matches; also include title matches
    seen_pdf_ids: set = set()
    results = []

    # 1) page text search (regex case-insensitive). For larger libs, MongoDB text index is better but regex works for partial words.
    cursor = db.pdf_pages.find(
        {"pdf_id": {"$in": list(accessible_pdf_ids)}, "text": {"$regex": safe_q, "$options": "i"}},
        {"_id": 0},
    ).limit(500)
    page_hits: Dict[str, dict] = {}
    async for pg in cursor:
        pid = pg["pdf_id"]
        if pid in page_hits:
            continue
        page_hits[pid] = pg

    for pid, pg in page_hits.items():
        if pid not in pdfs_meta:
            continue
        p = pdfs_meta[pid]
        snippet = make_snippet(pg["text"], q)
        results.append({
            "pdf_id": pid,
            "title": p.get("title", ""),
            "filename": p.get("filename", ""),
            "page": pg["page"],
            "snippet": snippet,
            "match_query": q,
            "source": pdf_source.get(pid, "personal"),
            "created_at": p.get("created_at"),
            "match_in": "content",
        })
        seen_pdf_ids.add(pid)

    # 2) title matches
    async for p in db.pdfs.find(
        {"id": {"$in": list(accessible_pdf_ids)}, "title": {"$regex": safe_q, "$options": "i"}},
        {"_id": 0},
    ):
        if p["id"] in seen_pdf_ids:
            continue
        results.append({
            "pdf_id": p["id"],
            "title": p.get("title", ""),
            "filename": p.get("filename", ""),
            "page": 1,
            "snippet": "",
            "match_query": q,
            "source": pdf_source.get(p["id"], "personal"),
            "created_at": p.get("created_at"),
            "match_in": "title",
        })
        seen_pdf_ids.add(p["id"])

    # dedup by content_hash: keep personal over shared
    by_hash: Dict[str, dict] = {}
    final: List[dict] = []
    for r in results:
        p = pdfs_meta.get(r["pdf_id"], {})
        h = p.get("content_hash") or r["pdf_id"]
        existing = by_hash.get(h)
        if not existing:
            by_hash[h] = r
            final.append(r)
        else:
            # prefer personal
            if existing["source"] != "personal" and r["source"] == "personal":
                final = [x for x in final if x is not existing]
                by_hash[h] = r
                final.append(r)
    final.sort(key=lambda x: (0 if x["match_in"] == "title" else 1, x.get("created_at") or ""), reverse=False)
    return {"results": final[:50]}


# ----------------- Shared Libraries -----------------
@api.post("/libraries")
async def create_library(payload: CreateLibraryIn, user_id: str = Depends(get_current_user_id)):
    lib_id = str(uuid.uuid4())
    share_token = secrets.token_urlsafe(16)
    doc = {
        "id": lib_id,
        "name": payload.name.strip() or "Libreria",
        "description": payload.description or "",
        "owner_id": user_id,
        "pdf_ids": [],
        "members": [],
        "share_token": share_token,
        "public": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.shared_libraries.insert_one(doc)
    await log_event("library.create", f"Libreria creata: {doc['name']}", user_id=user_id)
    doc.pop("_id", None)
    return doc


@api.get("/libraries")
async def list_libraries(user_id: str = Depends(get_current_user_id)):
    cursor = db.shared_libraries.find(
        {"$or": [{"owner_id": user_id}, {"members": user_id}]},
        {"_id": 0},
    ).sort("created_at", -1)
    items = await cursor.to_list(1000)
    return {"items": items}


@api.get("/libraries/{lib_id}")
async def get_library(lib_id: str, user_id: str = Depends(get_current_user_id)):
    lib = await db.shared_libraries.find_one({"id": lib_id}, {"_id": 0})
    if not lib:
        raise HTTPException(status_code=404, detail="Libreria non trovata")
    if lib["owner_id"] != user_id and user_id not in lib.get("members", []) and not lib.get("public"):
        raise HTTPException(status_code=403, detail="Accesso negato")
    pdfs = await db.pdfs.find({"id": {"$in": lib.get("pdf_ids", [])}}, {"_id": 0}).to_list(10000)
    lib["pdfs"] = [_serialize_pdf(p) for p in pdfs]
    lib["is_owner"] = lib["owner_id"] == user_id
    return lib


@api.post("/libraries/{lib_id}/pdfs")
async def add_to_library(lib_id: str, payload: AddPdfsIn, user_id: str = Depends(get_current_user_id)):
    lib = await db.shared_libraries.find_one({"id": lib_id, "owner_id": user_id}, {"_id": 0})
    if not lib:
        raise HTTPException(status_code=404, detail="Libreria non trovata")
    valid = await db.pdfs.find({"id": {"$in": payload.pdf_ids}, "owner_id": user_id}, {"_id": 0, "id": 1}).to_list(1000)
    valid_ids = [v["id"] for v in valid]
    await db.shared_libraries.update_one({"id": lib_id}, {"$addToSet": {"pdf_ids": {"$each": valid_ids}}})
    return {"ok": True, "added": valid_ids}


@api.delete("/libraries/{lib_id}/pdfs/{pdf_id}")
async def remove_from_library(lib_id: str, pdf_id: str, user_id: str = Depends(get_current_user_id)):
    res = await db.shared_libraries.update_one(
        {"id": lib_id, "owner_id": user_id},
        {"$pull": {"pdf_ids": pdf_id}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Libreria non trovata")
    return {"ok": True}


@api.delete("/libraries/{lib_id}")
async def delete_library(lib_id: str, user_id: str = Depends(get_current_user_id)):
    res = await db.shared_libraries.delete_one({"id": lib_id, "owner_id": user_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Libreria non trovata")
    await log_event("library.delete", f"Libreria cancellata: {lib_id}", user_id=user_id)
    return {"ok": True}


@api.get("/shared/{share_token}")
async def view_shared(share_token: str, request: Request, user_id: Optional[str] = Depends(get_optional_user_id)):
    lib = await db.shared_libraries.find_one({"share_token": share_token}, {"_id": 0})
    if not lib:
        raise HTTPException(status_code=404, detail="Link non valido o rimosso")
    if not user_id:
        # Frontend handles redirect to login - tell it auth is required
        raise HTTPException(status_code=401, detail="Login richiesto per accedere alla libreria condivisa")
    # add as member if not owner and not yet member
    if lib["owner_id"] != user_id and user_id not in lib.get("members", []):
        await db.shared_libraries.update_one({"id": lib["id"]}, {"$addToSet": {"members": user_id}})
        await log_event("share.access", f"Accesso libreria condivisa: {lib['name']}", user_id=user_id)
        lib["members"].append(user_id)
    pdfs = await db.pdfs.find({"id": {"$in": lib.get("pdf_ids", [])}}, {"_id": 0}).to_list(10000)
    lib["pdfs"] = [_serialize_pdf(p) for p in pdfs]
    lib["is_owner"] = lib["owner_id"] == user_id
    return lib


@api.post("/pdfs/{pdf_id}/import")
async def import_shared_pdf(pdf_id: str, user_id: str = Depends(get_current_user_id)):
    """Import a shared PDF into user's personal library (creates a copy)."""
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="PDF non trovato")
    if p["owner_id"] == user_id:
        return {"ok": True, "pdf_id": pdf_id, "already_owned": True}
    accessible = await _user_can_access_pdf(user_id, pdf_id)
    if not accessible:
        raise HTTPException(status_code=403, detail="Accesso negato")
    src = UPLOAD_DIR / p["owner_id"] / f"{pdf_id}.pdf"
    if not src.exists():
        raise HTTPException(status_code=404, detail="File mancante")
    new_id = str(uuid.uuid4())
    user_dir = UPLOAD_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    dst = user_dir / f"{new_id}.pdf"
    dst.write_bytes(src.read_bytes())
    new_doc = {**p, "id": new_id, "owner_id": user_id, "created_at": datetime.now(timezone.utc).isoformat()}
    new_doc.pop("_id", None)
    await db.pdfs.insert_one(new_doc)
    pages = await db.pdf_pages.find({"pdf_id": pdf_id}, {"_id": 0}).to_list(10000)
    if pages:
        new_pages = [{**pg, "pdf_id": new_id, "owner_id": user_id} for pg in pages]
        await db.pdf_pages.insert_many(new_pages)
    await log_event("pdf.import", f"Importato PDF condiviso: {p.get('title')}", user_id=user_id)
    return {"ok": True, "pdf_id": new_id}


# ----------------- Admin Logs -----------------
@api.post("/admin/logs")
async def admin_logs(
    payload: AdminPwdIn,
    q: Optional[str] = None,
    event_type: Optional[str] = None,
    sort: str = "date_desc",
    limit: int = 200,
):
    if payload.password != ADMIN_LOG_PASSWORD:
        raise HTTPException(status_code=401, detail="Password errata")
    flt: Dict[str, Any] = {}
    if event_type and event_type != "all":
        flt["event_type"] = {"$regex": f"^{re.escape(event_type)}", "$options": "i"}
    if q:
        flt["description"] = {"$regex": re.escape(q), "$options": "i"}
    sort_dir = -1 if sort == "date_desc" else 1
    cursor = db.app_logs.find(flt, {"_id": 0}).sort("created_at", sort_dir).limit(min(limit, 1000))
    items = await cursor.to_list(1000)
    types = await db.app_logs.distinct("event_type")
    return {"items": items, "types": sorted(types)}


# ---------------- Admin panel (admin user only) ----------------
ADMIN_EMAIL = "admin@scorelib.app"


async def require_admin(user_id: str = Depends(get_current_user_id)) -> str:
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    if u.get("email", "").lower() != ADMIN_EMAIL and not u.get("is_admin"):
        raise HTTPException(status_code=403, detail="Accesso solo amministratore")
    return user_id


@api.get("/admin/users")
async def admin_users(_: str = Depends(require_admin)):
    out = []
    async for u in db.users.find({}, {"_id": 0, "password_hash": 0, "google_refresh_token": 0}):
        pdf_count = await db.pdfs.count_documents({"owner_id": u["user_id"]})
        backed = await db.pdfs.count_documents({"owner_id": u["user_id"], "drive_file_id": {"$nin": [None, ""]}})
        is_google = bool(u.get("auth_provider") == "google" or u.get("google_email"))
        out.append({
            "user_id": u["user_id"],
            "email": u["email"],
            "name": u.get("name", ""),
            "auth_provider": u.get("auth_provider", "password"),
            "is_admin": u.get("is_admin", False) or u.get("email", "").lower() == ADMIN_EMAIL,
            "backup_enabled": u.get("backup_enabled", False),
            "drive_connected": is_google,
            "storage_type": "google_drive" if is_google else "local_only",
            "pdf_count": pdf_count,
            "backed_up_pdfs": backed,
            "last_backup_at": u.get("last_backup_at"),
            "created_at": u.get("created_at"),
        })
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {"users": out, "total": len(out)}


@api.get("/admin/stats")
async def admin_stats(_: str = Depends(require_admin)):
    users_total = await db.users.count_documents({})
    pdfs_total = await db.pdfs.count_documents({})
    google_users = await db.users.count_documents({"auth_provider": "google"})
    backed_pdfs = await db.pdfs.count_documents({"drive_file_id": {"$nin": [None, ""]}})
    libs = await db.shared_libraries.count_documents({})
    logs_24h = await db.app_logs.count_documents({"created_at": {"$gte": (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()}})
    errors_24h = await db.app_logs.count_documents({
        "created_at": {"$gte": (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()},
        "level": "error",
    })
    return {
        "users_total": users_total,
        "google_users": google_users,
        "local_users": users_total - google_users,
        "pdfs_total": pdfs_total,
        "backed_up_pdfs": backed_pdfs,
        "shared_libraries": libs,
        "events_24h": logs_24h,
        "errors_24h": errors_24h,
    }


# -------- Master Drive (system-wide backup account) --------

@api.get("/admin/master-drive/status")
async def master_drive_status(_: str = Depends(require_admin)):
    m = await get_master_drive()
    if not m:
        return {"connected": False}
    return {
        "connected": True,
        "email": m.get("email", ""),
        "folder_root_id": m.get("folder_root_id", ""),
    }


@api.post("/admin/master-drive/url")
async def master_drive_url(payload: GoogleAuthUrlIn, _: str = Depends(require_admin)):
    if not gi.google_configured():
        raise HTTPException(status_code=503, detail="Google OAuth non configurato")
    state = secrets.token_urlsafe(16)
    return {"url": gi.build_auth_url(payload.redirect_uri, state), "state": state}


@api.post("/admin/master-drive/connect")
async def master_drive_connect(payload: GoogleAuthIn, _: str = Depends(require_admin)):
    if not gi.google_configured():
        raise HTTPException(status_code=503, detail="Google OAuth non configurato")
    try:
        tokens = await gi.exchange_code(payload.code, payload.redirect_uri)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Codice Google non valido: {e}")
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Nessun refresh token ricevuto")
    info = await gi.fetch_userinfo(tokens.get("access_token"))
    email = (info.get("email") or "").lower().strip()
    folder_root_id = await asyncio.to_thread(gi.ensure_master_root, refresh_token)
    await set_master_drive({"refresh_token": refresh_token, "email": email, "folder_root_id": folder_root_id})
    await log_event("admin.master_drive.connect", f"Master Drive connesso: {email} · root={folder_root_id}", level="info")
    return {"connected": True, "email": email, "folder_root_id": folder_root_id}


@api.post("/admin/master-drive/disconnect")
async def master_drive_disconnect(_: str = Depends(require_admin)):
    await set_master_drive(None)
    await log_event("admin.master_drive.disconnect", "Master Drive disconnesso", level="warn")
    return {"connected": False}


@api.post("/admin/master-drive/test")
async def master_drive_test(_: str = Depends(require_admin)):
    m = await get_master_drive()
    if not m:
        raise HTTPException(status_code=400, detail="Master Drive non connesso")
    refresh = m["refresh_token"]
    try:
        root = m.get("folder_root_id") or await asyncio.to_thread(gi.ensure_master_root, refresh)
        test_data = b"%PDF-1.4\n%scorelib master test\n1 0 obj<<>>endobj\ntrailer<</Size 1>>\n%%EOF\n"
        drive_id = await asyncio.to_thread(gi.upload_to_drive, refresh, root, f"_master_test_{uuid.uuid4().hex[:6]}.pdf", test_data)
        files = await asyncio.to_thread(gi.list_drive_files, refresh, root)
        await asyncio.to_thread(gi.delete_from_drive, refresh, drive_id)
        await log_event("admin.master_drive.test", f"Test OK · root={root} · {len(files)} file totali", level="info")
        return {"ok": True, "folder_root_id": root, "files_in_root": len(files)}
    except Exception as e:
        await log_event("admin.master_drive.test.fail", f"Test fallito: {e}", level="error")
        raise HTTPException(status_code=502, detail=str(e))


# -------- end master drive --------


@api.post("/logs/client-error")
async def client_error(payload: dict, request: Request, user_id: Optional[str] = Depends(get_optional_user_id)):
    msg = (payload.get("message") or "")[:500]
    url = (payload.get("url") or "")[:300]
    stack = (payload.get("stack") or "")[:2000]
    cstack = (payload.get("component_stack") or "")[:2000]
    await log_event(
        "ui.error",
        f"Crash UI: {msg} @ {url}",
        user_id=user_id, level="error",
        meta={"stack": stack, "component_stack": cstack, "url": url},
    )
    return {"ok": True}


# ----------------- Health -----------------
@api.get("/")
async def root():
    return {"app": APP_NAME, "ok": True}


app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
