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

from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, UploadFile, File, Form, Query, BackgroundTasks
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

# Semaphore to limit concurrent heavy PDF processing (compress + OCR)
# Max 2 concurrent to prevent overload on small instances
pdf_processing_semaphore = asyncio.Semaphore(2)

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

APP_NAME = os.environ.get("APP_NAME", "ScoreLib")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@scorelib.app").lower()
UPLOAD_SESSION_TTL_SECONDS = int(os.environ.get("UPLOAD_SESSION_TTL_SECONDS", "3600"))
WORKER_SECRET = os.environ.get("WORKER_SECRET", "")

app = FastAPI(title=f"{APP_NAME} API")

DEFAULT_CORS_ORIGINS = "https://scorelib.vercel.app,https://boh-emerg-wzsa.vercel.app,http://localhost:3000"
allowed_origins = [
    origin.strip()
    for origin in os.environ.get("BACKEND_CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
]
allow_origin_regex = os.environ.get("BACKEND_CORS_ORIGIN_REGEX", r"^https://scorelib(-.*)?\.vercel\.app$")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

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
    await db.password_resets.create_index("token_hash", unique=True, sparse=True)
    await db.password_resets.create_index("expires_at", expireAfterSeconds=0)
    await db.app_logs.create_index([("created_at", -1)])
    await db.system_settings.create_index("key", unique=True)
    await db.upload_sessions.create_index("token_hash", unique=True)
    await db.upload_sessions.create_index("expires_at", expireAfterSeconds=0)
    await db.upload_jobs.create_index([("status", 1), ("created_at", 1)])
    await db.upload_jobs.create_index("pdf_id", unique=True)


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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat()


def safe_pdf_filename(name: str, fallback: str = "upload.pdf") -> str:
    base = (name or fallback).replace("\\", "/").split("/")[-1].strip()
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base)[:180].strip(" .")
    if not base:
        base = fallback
    if not base.lower().endswith(".pdf"):
        base = f"{base}.pdf"
    return base


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def resolve_upload_credentials(user_id: str) -> Optional[dict]:
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    return await resolve_backup_credentials(user or {})


async def ensure_drive_upload_folder(creds: dict, user_id: str) -> str:
    refresh = creds["refresh_token"]
    if creds["owner"] == "master":
        master = await get_master_drive() or {}
        root = master.get("folder_root_id")
        if not root:
            root = await asyncio.to_thread(gi.ensure_master_root, refresh)
            master["folder_root_id"] = root
            master["refresh_token"] = refresh
            await set_master_drive(master)
        return await asyncio.to_thread(gi.ensure_subfolder, refresh, root, user_id)
    folder = creds.get("folder_root_id")
    if not folder:
        folder = await asyncio.to_thread(gi.ensure_user_folder, refresh, user_id)
        await db.users.update_one({"user_id": user_id}, {"$set": {"drive_folder_id": folder}})
    return folder


async def get_drive_refresh_for_pdf(p: dict) -> Optional[str]:
    if p.get("drive_owner") == "master":
        master = await get_master_drive()
        return master.get("refresh_token") if master else None
    owner = await db.users.find_one({"user_id": p["owner_id"]}, {"_id": 0})
    return (owner or {}).get("google_refresh_token")


async def queue_pdf_processing(pdf_id: str, user_id: str) -> str:
    job_id = str(uuid.uuid4())
    now = iso_now()
    await db.upload_jobs.update_one(
        {"pdf_id": pdf_id},
        {"$setOnInsert": {
            "id": job_id,
            "pdf_id": pdf_id,
            "owner_id": user_id,
            "attempts": 0,
            "created_at": now,
        }, "$set": {"status": "queued", "updated_at": now, "error": None}},
        upsert=True,
    )
    job = await db.upload_jobs.find_one({"pdf_id": pdf_id}, {"_id": 0})
    return job["id"]


async def load_pdf_bytes_for_processing(p: dict) -> bytes:
    fpath = Path(p.get("file_path") or "")
    if fpath.exists():
        return await asyncio.to_thread(fpath.read_bytes)
    if p.get("drive_file_id"):
        refresh = await get_drive_refresh_for_pdf(p)
        if not refresh:
            raise RuntimeError("Credenziali Drive non disponibili")
        data = await asyncio.to_thread(gi.download_from_drive, refresh, p["drive_file_id"])
        fpath.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(fpath.write_bytes, data)
        return data
    raise RuntimeError("File non disponibile nello storage")


async def fail_stale_processing_jobs(max_age_seconds: int = 3600) -> None:
    cutoff = utcnow() - timedelta(seconds=max_age_seconds)
    stale_jobs = await db.upload_jobs.find({"status": "processing", "started_at": {"$lt": cutoff.isoformat()}}).to_list(1000)
    for job in stale_jobs:
        await db.upload_jobs.update_one(
            {"id": job["id"]},
            {"$set": {"status": "failed", "error": "processing_timeout", "updated_at": iso_now(), "finished_at": iso_now()}},
        )
        await db.pdfs.update_one(
            {"id": job["pdf_id"], "processing_status": "processing"},
            {"$set": {"processing_status": "failed", "processing_error": "Processing timeout"}},
        )
        await log_event(
            "pdf.error",
            f"Processing timeout: pdf_id={job['pdf_id']}",
            level="error",
            meta={"pdf_id": job["pdf_id"], "job_id": job["id"], "stage": "stale_timeout"},
        )


async def process_pdf_job(job_id: str) -> None:
    job = await db.upload_jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        return
    pdf_id = job["pdf_id"]
    now = iso_now()
    claimed = await db.upload_jobs.update_one(
        {"id": job_id, "status": {"$in": ["queued", "failed_retry"]}},
        {"$set": {"status": "processing", "started_at": now, "updated_at": now}, "$inc": {"attempts": 1}},
    )
    if claimed.matched_count == 0:
        return
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    if not p:
        await db.upload_jobs.update_one({"id": job_id}, {"$set": {"status": "failed", "error": "PDF non trovato", "updated_at": iso_now()}})
        return
    if p.get("processing_status") == "ready":
        await db.upload_jobs.update_one({"id": job_id}, {"$set": {"status": "done", "updated_at": iso_now(), "finished_at": iso_now()}})
        return
    user_id = p["owner_id"]
    await db.pdfs.update_one({"id": pdf_id}, {"$set": {"processing_status": "processing", "processing_error": None}})
    try:
        data = await load_pdf_bytes_for_processing(p)
        if data[:4] != b"%PDF":
            raise RuntimeError("Non e un PDF valido")
        original_size = len(data)
        content_hash = hashlib.sha256(data).hexdigest()
        dup = await db.pdfs.find_one(
            {"owner_id": user_id, "content_hash": content_hash, "id": {"$ne": pdf_id}, "processing_status": {"$ne": "failed"}},
            {"_id": 0, "id": 1, "title": 1},
        )
        if dup:
            await db.pdfs.update_one({"id": pdf_id}, {"$set": {
                "processing_status": "failed",
                "processing_error": "Questo PDF esiste gia nella tua libreria",
                "duplicate_of": dup["id"],
                "content_hash": content_hash,
            }})
            await db.upload_jobs.update_one({"id": job_id}, {"$set": {"status": "failed", "error": "duplicate", "updated_at": iso_now()}})
            await log_event("pdf.duplicate", f"Duplicato rilevato in background: {p.get('filename')}", user_id=user_id, level="warn", meta={"pdf_id": pdf_id, "existing_id": dup["id"]})
            return
        compressed_data, was_compressed = await asyncio.to_thread(compress_pdf, data)
        data = compressed_data
        pages_text, total_pages, used_ocr = await asyncio.to_thread(extract_pages, data)
        fpath = Path(p.get("file_path") or (UPLOAD_DIR / user_id / f"{pdf_id}.pdf"))
        fpath.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(fpath.write_bytes, data)
        page_docs = [{
            "pdf_id": pdf_id,
            "owner_id": user_id,
            "page": i + 1,
            "text": (t or "")[:50000],
        } for i, t in enumerate(pages_text)]
        await db.pdf_pages.delete_many({"pdf_id": pdf_id})
        if page_docs:
            await db.pdf_pages.insert_many(page_docs)
        storage_type = "google_drive" if p.get("drive_file_id") else "local"
        await db.pdfs.update_one({"id": pdf_id}, {"$set": {
            "size": len(data),
            "original_size": original_size,
            "compressed": was_compressed,
            "pages": total_pages,
            "used_ocr": used_ocr,
            "content_hash": content_hash,
            "storage_type": storage_type,
            "file_path": str(fpath.resolve()),
            "processing_status": "ready",
            "processing_error": None,
            "processed_at": iso_now(),
        }})
        await db.upload_jobs.update_one({"id": job_id}, {"$set": {"status": "done", "updated_at": iso_now(), "finished_at": iso_now()}})
        await log_event("pdf.upload", f"Ricevuto e indicizzato: {p.get('filename')} ({total_pages}pp{', OCR' if used_ocr else ''})", user_id=user_id, meta={"pdf_id": pdf_id, "pages": total_pages, "ocr": used_ocr, "compressed": was_compressed, "storage_type": storage_type})
    except Exception as e:
        logger.exception("background pdf processing failed")
        await db.pdfs.update_one({"id": pdf_id}, {"$set": {"processing_status": "failed", "processing_error": str(e)[:500]}})
        await db.upload_jobs.update_one({"id": job_id}, {"$set": {"status": "failed", "error": str(e)[:500], "updated_at": iso_now(), "finished_at": iso_now()}})
        await log_event("pdf.error", f"Indicizzazione fallita per {p.get('filename')}: {e}", user_id=user_id, level="error", meta={"pdf_id": pdf_id, "stage": "background_process"})


async def require_admin(user_id: str = Depends(get_current_user_id)) -> str:
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    if u.get("email", "").lower() != ADMIN_EMAIL and not u.get("is_admin"):
        raise HTTPException(status_code=403, detail="Accesso solo amministratore")
    return user_id


async def seed_admin():
    seeds = [
        ("admin@scorelib.app", "Admin02009!", "Admin"),
        ("admin@test.local", "Admin02009!", "Admin Test"),
    ]
    for email, password, name in seeds:
        existing = await db.users.find_one({"email": email}, {"_id": 0, "user_id": 1})
        if existing:
            await db.users.update_one({"email": email}, {"$set": {"is_admin": True, "profile_completed": True}})
            continue
        user_id = f"user_admin_{uuid.uuid4().hex[:8]}"
        await db.users.insert_one({
            "user_id": user_id,
            "email": email,
            "password_hash": hash_password(password),
            "name": name,
            "picture": "",
            "how_found": "seed",
            "backup_enabled": email == "admin@scorelib.app",
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
    """MVP auth has no email verification gate: unblock legacy/unverified users."""
    n = 0
    async for u in db.users.find({"$or": [{"email_verified": {"$exists": False}}, {"email_verified": False}]}, {"_id": 0, "user_id": 1}):
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


class PdfPatchIn(BaseModel):
    title: Optional[str] = None
    is_favorite: Optional[bool] = None
    tags: Optional[List[str]] = None


class CreatePdfIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class PresignUploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=1)
    content_type: Optional[str] = "application/pdf"
    content_hash: Optional[str] = None


class CompleteUploadIn(BaseModel):
    pdf_id: str
    drive_file_id: Optional[str] = None
    size: Optional[int] = None


# ----------------- Auth -----------------
def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
        "email_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(doc)
    token = create_jwt(user_id)
    await log_event("auth.register", f"Nuovo account creato: {email}", user_id=user_id, meta={"ip": ip})
    return {"token": token, "user": user_public(doc), "message": "Account creato"}


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
        token_hash = hash_reset_token(token)
        await db.password_resets.insert_one({
            "token": token_hash,  # hashed value kept for compatibility with the legacy unique index
            "token_hash": token_hash,
            "user_id": u["user_id"],
            "email": email,
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=60),
            "created_at": datetime.now(timezone.utc),
        })
        frontend_url = request.headers.get("origin") or request.headers.get("referer", "").rstrip("/")
        if not frontend_url:
            frontend_url = ""
        reset_link = f"{frontend_url}/reset?token={token}"
        sent = await send_password_reset_email(email, reset_link, u.get("name", ""))
        if sent:
            await log_event("password_reset_requested", f"Reset password richiesto per {email}", user_id=u["user_id"])
            if not os.environ.get("RESEND_API_KEY"):
                await log_event("password_reset.dev_link", f"DEV reset link per {email}: {reset_link}", user_id=u["user_id"], level="warn", meta={"reset_link": reset_link})
        else:
            await log_event("password_reset_failed", f"Invio reset password fallito per {email}", user_id=u["user_id"], level="error")
            return {"ok": False, "message": "Non siamo riusciti a inviare l'email. Riprova piu tardi."}
    return {"ok": True, "message": "Se l'email esiste, riceverai un link per il reset."}


@api.post("/auth/reset")
async def reset_password(payload: ResetIn):
    token_hash = hash_reset_token(payload.token)
    rec = await db.password_resets.find_one({"$or": [{"token_hash": token_hash}, {"token": payload.token}]}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=400, detail="Link non valido o scaduto")
    exp = rec["expires_at"]
    if isinstance(exp, str):
        exp = datetime.fromisoformat(exp)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        await db.password_resets.delete_one({"$or": [{"token_hash": token_hash}, {"token": payload.token}]})
        raise HTTPException(status_code=400, detail="Link scaduto")
    new_hash = hash_password(payload.password)
    await db.users.update_one({"user_id": rec["user_id"]}, {"$set": {"password_hash": new_hash}})
    await db.password_resets.delete_one({"$or": [{"token_hash": token_hash}, {"token": payload.token}]})
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
            "backup_enabled": bool(refresh_token),
            "profile_completed": False,
            "auth_provider": "google",
            "google_refresh_token": refresh_token or "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.users.insert_one(u)
        await log_event("auth.google.new", f"Nuovo utente Google: {email}", user_id=user_id)
    else:
        upd = {
            "auth_provider": "google" if refresh_token else u.get("auth_provider", "google"),
            "picture": picture or u.get("picture", ""),
            "name": u.get("name") or name,
        }
        if refresh_token:
            upd["google_refresh_token"] = refresh_token
            upd["backup_enabled"] = True
        await db.users.update_one({"user_id": u["user_id"]}, {"$set": upd})
        await log_event("auth.google.login", f"Login Google: {email}", user_id=u["user_id"])
        u = await db.users.find_one({"user_id": u["user_id"]}, {"_id": 0})

    if refresh_token and not u.get("drive_folder_id"):
        try:
            folder_id = await asyncio.to_thread(gi.ensure_user_folder, refresh_token, u["user_id"])
            await db.users.update_one({"user_id": u["user_id"]}, {"$set": {"drive_folder_id": folder_id, "backup_enabled": True}})
            await log_event("drive.folder", f"Cartella Drive pronta: /ScoreLib/{u['user_id']} folder={folder_id}", user_id=u["user_id"], meta={"folder_id": folder_id})
            u = await db.users.find_one({"user_id": u["user_id"]}, {"_id": 0})
        except Exception as e:
            await log_event("drive.folder.error", f"Creazione cartella Drive fallita: {e}", user_id=u["user_id"], level="error")

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
    creds = await resolve_backup_credentials(u or {})
    if not u or not creds:
        raise HTTPException(status_code=400, detail="Drive non connesso. Connetti Google Drive o chiedi all'admin di collegare il Master Drive.")
    refresh = creds["refresh_token"]
    folder_id = u.get("drive_folder_id") if creds["owner"] == "user" else None
    try:
        if creds["owner"] == "master":
            master = await get_master_drive() or {}
            root = master.get("folder_root_id") or await asyncio.to_thread(gi.ensure_master_root, refresh)
            if root != master.get("folder_root_id"):
                master["folder_root_id"] = root
                master["refresh_token"] = refresh
                await set_master_drive(master)
            folder_id = await asyncio.to_thread(gi.ensure_subfolder, refresh, root, user_id)
        elif not folder_id:
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
            errors += 1
            await log_event("pdf.error", f"Backup saltato, file locale mancante: {p.get('title')}", user_id=user_id, level="error", meta={"pdf_id": p["id"], "stage": "backup_run", "file_path": str(fpath.resolve())})
            continue
        try:
            data = fpath.read_bytes()
            drive_id = await asyncio.to_thread(gi.upload_to_drive, refresh, folder_id, f"{p['id']}.pdf", data)
            synced_at = datetime.now(timezone.utc).isoformat()
            await db.pdfs.update_one({"id": p["id"]}, {"$set": {
                "drive_file_id": drive_id,
                "drive_owner": creds["owner"],
                "storage_type": "google_drive",
                "synced_at": synced_at,
            }})
            uploaded += 1
            await log_event("pdf.sync", f"Backup Drive completato: {p.get('title')} - fileId={drive_id}", user_id=user_id, meta={"pdf_id": p["id"], "drive_file_id": drive_id, "folder_id": folder_id, "drive_owner": creds["owner"], "synced_at": synced_at})
            await log_event("pdf.storage", f"Storage finale: GOOGLE_DRIVE - driveFileId={drive_id} - localCache={str(fpath.resolve())}", user_id=user_id, meta={"pdf_id": p["id"], "storage_type": "google_drive", "drive_file_id": drive_id, "file_path": str(fpath.resolve())})
        except Exception as e:
            errors += 1
            await log_event("backup.drive.error", f"Upload {p.get('title')}: {e}", user_id=user_id, level="error")
            await log_event("pdf.error", f"Backup Drive fallito per {p.get('title')}: {e}", user_id=user_id, level="error", meta={"pdf_id": p["id"], "stage": "backup_run", "error": str(e)})
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.users.update_one({"user_id": user_id}, {"$set": {"last_backup_at": now_iso}})
    await log_event("backup.run", f"Backup completato: {uploaded} file caricati, {errors} errori", user_id=user_id)
    return {"ok": True, "uploaded": uploaded, "errors": errors, "last_backup_at": now_iso}


@api.get("/backup/status")
async def backup_status(user_id: str = Depends(get_current_user_id)):
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    master = await get_master_drive()
    has_user_drive = bool(u.get("google_refresh_token"))
    has_master_drive = bool(master and master.get("refresh_token"))
    total = await db.pdfs.count_documents({"owner_id": user_id})
    backed = await db.pdfs.count_documents({"owner_id": user_id, "drive_file_id": {"$nin": [None, ""]}})
    return {
        "backup_enabled": u.get("backup_enabled", False),
        "drive_connected": has_user_drive or has_master_drive,
        "user_drive_connected": has_user_drive,
        "master_drive_connected": has_master_drive,
        "drive_email": u.get("google_email", "") or ((master or {}).get("email", "") if has_master_drive else ""),
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
    if not u:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    if not u.get("is_admin"):
        raise HTTPException(status_code=403, detail="Solo admin")
    creds = await resolve_backup_credentials(u)
    if not creds:
        raise HTTPException(status_code=400, detail="Drive non connesso")
    refresh = creds["refresh_token"]
    try:
        if creds["owner"] == "master":
            master = await get_master_drive() or {}
            root = master.get("folder_root_id") or await asyncio.to_thread(gi.ensure_master_root, refresh)
            if root != master.get("folder_root_id"):
                master["folder_root_id"] = root
                master["refresh_token"] = refresh
                await set_master_drive(master)
            folder_id = await asyncio.to_thread(gi.ensure_subfolder, refresh, root, user_id)
        else:
            folder_id = u.get("drive_folder_id") or await asyncio.to_thread(gi.ensure_user_folder, refresh, user_id)
            if folder_id != u.get("drive_folder_id"):
                await db.users.update_one({"user_id": user_id}, {"$set": {"drive_folder_id": folder_id}})
        test_data = b"%PDF-1.4\n%scorelib backup test\n1 0 obj<<>>endobj\ntrailer<</Size 1>>\n%%EOF\n"
        drive_id = await asyncio.to_thread(gi.upload_to_drive, refresh, folder_id, f"_scorelib_test_{uuid.uuid4().hex[:6]}.pdf", test_data)
        files = await asyncio.to_thread(gi.list_drive_files, refresh, folder_id)
        await asyncio.to_thread(gi.delete_from_drive, refresh, drive_id)
        await log_event("backup.test", f"Test backup OK - owner={creds['owner']} - folder={folder_id} - {len(files)} file(s)", user_id=user_id)
        return {"ok": True, "folder_id": folder_id, "files_count": len(files), "test_file_id": drive_id, "drive_owner": creds["owner"]}
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
@api.post("/pdfs/create")
async def create_blank_pdf(payload: CreatePdfIn, user_id: str = Depends(get_current_user_id)):
    title = payload.title.strip() or "Nuovo PDF"
    filename = title if title.lower().endswith(".pdf") else f"{title}.pdf"
    pdf_id = str(uuid.uuid4())
    user_dir = UPLOAD_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    fpath = user_dir / f"{pdf_id}.pdf"

    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        c.setTitle(title)
        c.showPage()
        c.save()
        data = buffer.getvalue()
    except Exception as e:
        await log_event("pdf.error", f"Creazione PDF vuoto fallita: {e}", user_id=user_id, level="error", meta={"stage": "create_blank"})
        raise HTTPException(status_code=500, detail="Impossibile creare il PDF")

    fpath.write_bytes(data)
    file_path_str = str(fpath.resolve())
    content_hash = hashlib.sha256(data).hexdigest()
    now_iso = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": pdf_id,
        "owner_id": user_id,
        "title": title[:-4] if title.lower().endswith(".pdf") else title,
        "filename": filename,
        "size": len(data),
        "original_size": len(data),
        "compressed": False,
        "pages": 1,
        "used_ocr": False,
        "content_hash": content_hash,
        "drive_file_id": None,
        "storage_type": "local",
        "file_path": file_path_str,
        "synced_at": None,
        "created_at": now_iso,
    }
    await db.pdfs.insert_one(doc)
    await db.pdf_pages.insert_one({"pdf_id": pdf_id, "owner_id": user_id, "page": 1, "text": ""})
    await log_event("pdf.save", f"PDF vuoto creato su disco: {file_path_str}", user_id=user_id, meta={"pdf_id": pdf_id, "filename": filename, "path": file_path_str, "size": len(data)})

    user_doc = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    drive_uploaded = False
    drive_id = None
    if user_doc and user_doc.get("backup_enabled"):
        creds = await resolve_backup_credentials(user_doc)
        if creds:
            try:
                refresh = creds["refresh_token"]
                if creds["owner"] == "master":
                    master = await get_master_drive() or {}
                    master_root = master.get("folder_root_id") or await asyncio.to_thread(gi.ensure_master_root, refresh)
                    master["folder_root_id"] = master_root
                    master["refresh_token"] = refresh
                    await set_master_drive(master)
                    user_folder = await asyncio.to_thread(gi.ensure_subfolder, refresh, master_root, user_id)
                else:
                    user_folder = creds.get("folder_root_id") or await asyncio.to_thread(gi.ensure_user_folder, refresh, user_id)
                    if user_folder != user_doc.get("drive_folder_id"):
                        await db.users.update_one({"user_id": user_id}, {"$set": {"drive_folder_id": user_folder}})
                drive_id = await asyncio.to_thread(gi.upload_to_drive, refresh, user_folder, f"{pdf_id}.pdf", data)
                sync_iso = datetime.now(timezone.utc).isoformat()
                await db.pdfs.update_one({"id": pdf_id}, {"$set": {
                    "drive_file_id": drive_id,
                    "drive_owner": creds["owner"],
                    "storage_type": "google_drive",
                    "synced_at": sync_iso,
                }})
                doc.update({"drive_file_id": drive_id, "drive_owner": creds["owner"], "storage_type": "google_drive", "synced_at": sync_iso})
                drive_uploaded = True
                await log_event("pdf.sync", f"PDF vuoto sincronizzato su Google Drive ({creds['owner'].upper()}) - fileId={drive_id}", user_id=user_id, meta={"pdf_id": pdf_id, "drive_file_id": drive_id, "folder_id": user_folder, "filename": filename})
            except Exception as e:
                await log_event("pdf.error", f"Sync Drive fallito per PDF vuoto {filename}: {e}", user_id=user_id, level="error", meta={"pdf_id": pdf_id, "filename": filename, "error": str(e), "stage": "drive_sync"})

    await log_event(
        "pdf.storage",
        f"Storage finale: {'GOOGLE_DRIVE - driveFileId=' + drive_id if drive_uploaded else 'LOCAL - path=' + file_path_str}",
        user_id=user_id,
        meta={"pdf_id": pdf_id, "storage_type": "google_drive" if drive_uploaded else "local", "drive_file_id": drive_id, "file_path": file_path_str},
    )
    await log_event("pdf.upload", f"Creato PDF vuoto: {filename} -> {('GOOGLE_DRIVE' if drive_uploaded else 'LOCAL')}", user_id=user_id, meta={"pdf_id": pdf_id, "filename": filename, "pages": 1, "ocr": False, "storage_type": doc["storage_type"], "drive_file_id": drive_id, "file_path": file_path_str})
    return _serialize_pdf(doc)


@api.post("/pdfs/upload-url")
async def create_pdf_upload_url(payload: PresignUploadIn, request: Request, user_id: str = Depends(get_current_user_id)):
    filename = safe_pdf_filename(payload.filename)
    if payload.content_type and payload.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Carica solo file PDF")
    if payload.content_hash:
        dup = await db.pdfs.find_one({"owner_id": user_id, "content_hash": payload.content_hash}, {"_id": 0, "id": 1, "title": 1})
        if dup:
            return {"duplicate": True, "existing_id": dup["id"], "existing_title": dup.get("title", ""), "error": "Questo PDF esiste gia nella tua libreria"}

    pdf_id = str(uuid.uuid4())
    user_dir = UPLOAD_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    fpath = user_dir / f"{pdf_id}.pdf"
    title = filename.rsplit(".", 1)[0]
    now = iso_now()
    doc = {
        "id": pdf_id,
        "owner_id": user_id,
        "title": title,
        "filename": filename,
        "size": payload.size,
        "original_size": payload.size,
        "compressed": False,
        "pages": 0,
        "used_ocr": False,
        "content_hash": payload.content_hash,
        "drive_file_id": None,
        "drive_owner": None,
        "drive_folder_id": None,
        "storage_type": "local",
        "file_path": str(fpath.resolve()),
        "synced_at": None,
        "processing_status": "uploading",
        "processing_error": None,
        "failed_reason": None,
        "created_at": now,
    }

    storage_type = "local"
    upload_url = None
    upload_headers = {"Content-Type": "application/pdf"}
    creds = await resolve_upload_credentials(user_id)
    if creds and gi.google_configured():
        try:
            # Prepare Drive target info but DO NOT create a resumable session for direct browser upload.
            # Direct client->Google resumable uploads require the same OAuth token used to create the session,
            # which we don't expose to the browser. To avoid CORS/authorization failures, we proxy uploads
            # through the backend: client uploads to our /uploads/{token} endpoint and the backend will
            # push the file to Google Drive after receiving it.
            folder_id = await ensure_drive_upload_folder(creds, user_id)
            # Keep storage_type local until server-side Drive sync succeeds.
            doc.update({
                "drive_owner": creds["owner"],
                "drive_folder_id": folder_id,
            })
        except Exception as e:
            await log_event("pdf.error", f"Preparazione Drive upload fallita, fallback locale: {e}", user_id=user_id, level="error", meta={"filename": filename, "stage": "upload_sign"})

    if not upload_url:
        raw_token = secrets.token_urlsafe(32)
        expires_at = utcnow() + timedelta(seconds=UPLOAD_SESSION_TTL_SECONDS)
        await db.upload_sessions.insert_one({
            "id": str(uuid.uuid4()),
            "token_hash": token_hash(raw_token),
            "pdf_id": pdf_id,
            "owner_id": user_id,
            "file_path": str(fpath.resolve()),
            "expected_size": payload.size,
            "status": "created",
            "created_at": now,
            "expires_at": expires_at,
        })
        upload_url = str(request.url_for("put_signed_upload", token=raw_token))
        # Keep google_drive as the final storage_type when credentials are available.
        # The browser still uploads to our proxy endpoint, and the backend will
        # push the file to Drive after receiving it.
        if storage_type == "local":
            doc["storage_type"] = "local"
        else:
            doc["storage_type"] = "google_drive"

    await db.pdfs.insert_one(doc)
    await log_event("pdf.upload.session", f"URL upload creata: {filename} -> {storage_type}", user_id=user_id, meta={"pdf_id": pdf_id, "storage_type": storage_type, "size": payload.size})
    return {
        "ok": True,
        "pdf_id": pdf_id,
        # Always return our internal PUT endpoint so the browser uploads to the backend proxy.
        "upload_url": upload_url,
        "upload_method": "PUT",
        "upload_headers": upload_headers,
        "storage_type": storage_type,
        "status": "uploading",
    }


@api.put("/uploads/{token}", name="put_signed_upload")
async def put_signed_upload(token: str, request: Request):
    session = await db.upload_sessions.find_one({"token_hash": token_hash(token)}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Upload non valido o scaduto")
    exp = session.get("expires_at")
    if isinstance(exp, str):
        exp = datetime.fromisoformat(exp)
    if exp and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp and exp < utcnow():
        raise HTTPException(status_code=410, detail="Upload scaduto")
    if session.get("status") == "uploaded":
        return {"ok": True, "bytes": session.get("bytes", 0)}

    fpath = Path(session["file_path"])
    fpath.parent.mkdir(parents=True, exist_ok=True)
    tmp = fpath.with_suffix(".uploading")
    written = 0
    first = b""
    try:
        with open(tmp, "wb") as out:
            async for chunk in request.stream():
                if not chunk:
                    continue
                if len(first) < 4:
                    first += chunk[: 4 - len(first)]
                out.write(chunk)
                written += len(chunk)
        if first != b"%PDF":
            tmp.unlink(missing_ok=True)
            await db.upload_sessions.update_one({"id": session["id"]}, {"$set": {"status": "failed", "error": "invalid_pdf", "updated_at": iso_now()}})
            await db.pdfs.update_one({"id": session["pdf_id"]}, {"$set": {"processing_status": "failed", "processing_error": "Upload non è un PDF valido"}})
            raise HTTPException(status_code=400, detail="Non e un PDF valido")
        expected_size = session.get("expected_size")
        if expected_size and written != expected_size:
            tmp.unlink(missing_ok=True)
            await db.upload_sessions.update_one({"id": session["id"]}, {"$set": {"status": "failed", "error": f"size_mismatch {written}/{expected_size}", "updated_at": iso_now()}})
            await db.pdfs.update_one({"id": session["pdf_id"]}, {"$set": {"processing_status": "failed", "processing_error": "Upload incompleto o interrotto"}})
            raise HTTPException(status_code=400, detail="Upload incompleto: dimensione file non corrisponde")
        tmp.replace(fpath)
    except HTTPException:
        raise
    except Exception as e:
        tmp.unlink(missing_ok=True)
        await db.upload_sessions.update_one({"id": session["id"]}, {"$set": {"status": "failed", "error": str(e)[:500], "updated_at": iso_now()}})
        raise HTTPException(status_code=500, detail="Upload fallito")

    await db.upload_sessions.update_one({"id": session["id"]}, {"$set": {"status": "uploaded", "bytes": written, "uploaded_at": iso_now(), "updated_at": iso_now()}})
    await db.pdfs.update_one({"id": session["pdf_id"]}, {"$set": {"size": written, "processing_status": "uploaded", "processing_error": None, "failed_reason": None}})
    await log_event("pdf.uploaded", f"Upload ricevuto: {session['pdf_id']} ({written} bytes)", user_id=session.get("owner_id"), meta={"pdf_id": session['pdf_id'], "bytes": written})
    return {"ok": True, "bytes": written}


@api.post("/pdfs/upload-complete")
async def complete_pdf_upload(payload: CompleteUploadIn, background_tasks: BackgroundTasks, user_id: str = Depends(get_current_user_id)):
    p = await db.pdfs.find_one({"id": payload.pdf_id, "owner_id": user_id}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="PDF non trovato")
    session = await db.upload_sessions.find_one({"pdf_id": payload.pdf_id}, {"_id": 0, "status": 1, "bytes": 1})
    if not session or session.get("status") != "uploaded":
        raise HTTPException(status_code=400, detail="Upload non completato")
    if p.get("processing_status") not in ("uploaded", "failed"):
        return {"ok": True, "status": p.get("processing_status", "queued"), "pdf": _serialize_pdf(p)}

    update = {
        "processing_status": "queued",
        "processing_error": None,
        "failed_reason": None,
        "received_at": iso_now(),
    }
    if payload.size is not None:
        if session.get("bytes") is not None and payload.size != session["bytes"]:
            raise HTTPException(status_code=400, detail="Dimensione upload non corrisponde")
        update["size"] = session.get("bytes") or payload.size
    elif session.get("bytes") is not None:
        update["size"] = session["bytes"]
    if p.get("storage_type") == "google_drive":
        if payload.drive_file_id:
            update["drive_file_id"] = payload.drive_file_id
            update["synced_at"] = iso_now()
        else:
            # Client did not provide a Drive file id. Attempt server-side upload from local cache.
            # This avoids exposing Google resumable sessions to the browser (CORS/authorization issues).
            fpath = Path(p.get("file_path") or "")
            if not fpath.exists():
                raise HTTPException(status_code=400, detail="Upload non completato")
            # Obtain refresh token for drive upload
            refresh = await get_drive_refresh_for_pdf(p)
            if not refresh:
                raise HTTPException(status_code=400, detail="Drive credentials non disponibili; riprova")
            folder_id = p.get("drive_folder_id")
            if not folder_id:
                # Ensure folder on drive for this user
                creds = await resolve_upload_credentials(user_id)
                if not creds:
                    raise HTTPException(status_code=400, detail="Drive folder non disponibile")
                folder_id = await ensure_drive_upload_folder(creds, user_id)
            # Read file bytes and upload to Drive in thread
            try:
                data = await asyncio.to_thread(lambda: fpath.read_bytes())
                drive_id = await upload_to_drive_with_retry(refresh, folder_id, fpath.name, data)
                update["drive_file_id"] = drive_id
                update["synced_at"] = iso_now()
                # update storage_type just in case
                update["storage_type"] = "google_drive"
                update["failed_reason"] = None
            except Exception as e:
                await log_event("pdf.error", f"Drive upload server-side fallito: {e}", user_id=user_id, level="error", meta={"pdf_id": payload.pdf_id})
                update["storage_type"] = "local"
                update["drive_file_id"] = None
                update["drive_owner"] = None
                update["processing_error"] = str(e)[:500]
                update["failed_reason"] = "drive_upload_failed"
                await log_event("pdf.storage", f"Drive upload fallito, fallback su LOCAL: {payload.pdf_id}", user_id=user_id, level="warn", meta={"pdf_id": payload.pdf_id, "reason": str(e)[:200]})
    else:
        fpath = Path(p.get("file_path") or "")
        if not fpath.exists():
            raise HTTPException(status_code=400, detail="Upload non completato")

    await db.pdfs.update_one({"id": payload.pdf_id}, {"$set": update})
    await log_event("pdf.queued", f"PDF messo in coda per processing: {payload.pdf_id}", user_id=user_id, meta={"pdf_id": payload.pdf_id, "status": "queued"})
    job_id = await queue_pdf_processing(payload.pdf_id, user_id)
    background_tasks.add_task(process_pdf_job, job_id)
    updated = await db.pdfs.find_one({"id": payload.pdf_id}, {"_id": 0})
    await log_event("pdf.received", f"File ricevuto, indicizzazione in coda: {updated.get('filename')}", user_id=user_id, meta={"pdf_id": payload.pdf_id, "job_id": job_id})
    return {"ok": True, "status": "received", "processing_status": "queued", "pdf": _serialize_pdf(updated)}


@api.post("/jobs/process-next")
@api.get("/jobs/process-next")
async def process_next_upload_job(secret: Optional[str] = Query(None)):
    if WORKER_SECRET and secret != WORKER_SECRET:
        raise HTTPException(status_code=403, detail="Worker secret non valido")
    if not WORKER_SECRET:
        raise HTTPException(status_code=503, detail="WORKER_SECRET non configurato")
    await fail_stale_processing_jobs()
    job = await db.upload_jobs.find_one({"status": {"$in": ["queued", "failed_retry"]}}, {"_id": 0}, sort=[("created_at", 1)])
    if not job:
        return {"ok": True, "processed": False}
    await process_pdf_job(job["id"])
    refreshed = await db.upload_jobs.find_one({"id": job["id"]}, {"_id": 0})
    return {"ok": True, "processed": True, "job": refreshed}


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
            # Async-safe compress + OCR with concurrency limit
            async with pdf_processing_semaphore:
                compressed_data, was_compressed = await asyncio.to_thread(compress_pdf, data)
                data = compressed_data
                # extract text + OCR
                try:
                    pages_text, total_pages, used_ocr = await asyncio.to_thread(extract_pages, data)
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
                "processing_status": "ready",
                "processing_error": None,
                "failed_reason": None,
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
        "processing_status": p.get("processing_status", "ready"),
        "processing_error": p.get("processing_error"),
        "failed_reason": p.get("failed_reason"),
        "duplicate_of": p.get("duplicate_of"),
        "processed_at": p.get("processed_at"),
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
        refresh = None
        if p.get("drive_owner") == "master":
            master = await get_master_drive()
            refresh = master.get("refresh_token") if master else None
        else:
            u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
            refresh = (u or {}).get("google_refresh_token")
        if refresh:
            try:
                await asyncio.to_thread(gi.delete_from_drive, refresh, p["drive_file_id"])
                await log_event("pdf.sync", f"Eliminato da Drive: {p.get('title')} - fileId={p['drive_file_id']}", user_id=user_id, meta={"pdf_id": pdf_id, "drive_file_id": p["drive_file_id"], "stage": "drive_delete"})
            except Exception as e:
                logger.warning(f"Drive delete failed: {e}")
                await log_event("pdf.error", f"Eliminazione Drive fallita per {p.get('title')}: {e}", user_id=user_id, level="error", meta={"pdf_id": pdf_id, "drive_file_id": p.get("drive_file_id"), "stage": "drive_delete"})
    await db.pdfs.delete_one({"id": pdf_id})
    await db.pdf_pages.delete_many({"pdf_id": pdf_id})
    await db.shared_libraries.update_many({"owner_id": user_id}, {"$pull": {"pdf_ids": pdf_id}})
    await log_event("pdf.delete", f"Eliminato PDF: {p.get('title')}", user_id=user_id)
    return {"ok": True}


async def _user_can_access_pdf(user_id: str, pdf_id: str) -> bool:
    """User can access a non-owned pdf if it belongs to a shared library they have access to."""
    libs = await db.shared_libraries.find(
        {"pdf_ids": pdf_id, "hidden_by_users": {"$ne": user_id}, "$or": [{"owner_id": user_id}, {"members": user_id}, {"public": True}]},
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
    lib_filter = {"hidden_by_users": {"$ne": user_id}, "$or": [{"owner_id": user_id}, {"members": user_id}, {"public": True}]}
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
        {"$or": [{"owner_id": user_id}, {"members": user_id}], "hidden_by_users": {"$ne": user_id}},
        {"_id": 0},
    ).sort("created_at", -1)
    items = await cursor.to_list(1000)
    return {"items": items}


@api.get("/libraries/hidden")
async def list_hidden_libraries(user_id: str = Depends(get_current_user_id)):
    cursor = db.shared_libraries.find(
        {"hidden_by_users": user_id},
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


@api.post("/libraries/{lib_id}/hide")
async def hide_library(lib_id: str, user_id: str = Depends(get_current_user_id)):
    lib = await db.shared_libraries.find_one({"id": lib_id}, {"_id": 0})
    if not lib:
        raise HTTPException(status_code=404, detail="Libreria non trovata")
    if lib["owner_id"] == user_id:
        raise HTTPException(status_code=403, detail="Il proprietario non può nascondere la propria libreria")
    if user_id not in lib.get("members", []):
        raise HTTPException(status_code=403, detail="Solo membri possono nascondere la libreria")
    await db.shared_libraries.update_one({"id": lib_id}, {"$addToSet": {"hidden_by_users": user_id}})
    await log_event("shared_library_hidden", f"Libreria nascosta: {lib['name']}", user_id=user_id)
    return {"ok": True}


@api.delete("/libraries/{lib_id}/hide")
async def unhide_library(lib_id: str, user_id: str = Depends(get_current_user_id)):
    lib = await db.shared_libraries.find_one({"id": lib_id}, {"_id": 0})
    if not lib:
        raise HTTPException(status_code=404, detail="Libreria non trovata")
    await db.shared_libraries.update_one({"id": lib_id}, {"$pull": {"hidden_by_users": user_id}})
    await log_event("shared_library_restored", f"Libreria ripristinata: {lib['name']}", user_id=user_id)
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
    if p.get("content_hash"):
        existing = await db.pdfs.find_one({"owner_id": user_id, "content_hash": p["content_hash"]}, {"_id": 0, "id": 1})
        if existing:
            return {"ok": True, "pdf_id": existing["id"], "already_owned": True}
    src = UPLOAD_DIR / p["owner_id"] / f"{pdf_id}.pdf"
    data = None
    if src.exists():
        data = src.read_bytes()
    elif p.get("drive_file_id"):
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
            except Exception as e:
                await log_event("pdf.error", f"Import da Drive fallito: {e}", user_id=user_id, level="error", meta={"pdf_id": pdf_id, "drive_file_id": p.get("drive_file_id"), "stage": "import_drive_download"})
    if data is None:
        raise HTTPException(status_code=404, detail="File mancante")
    new_id = str(uuid.uuid4())
    user_dir = UPLOAD_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    dst = user_dir / f"{new_id}.pdf"
    dst.write_bytes(data)
    file_path_str = str(dst.resolve())
    new_doc = {
        **p,
        "id": new_id,
        "owner_id": user_id,
        "drive_file_id": None,
        "drive_owner": None,
        "storage_type": "local",
        "file_path": file_path_str,
        "synced_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    new_doc.pop("_id", None)
    await db.pdfs.insert_one(new_doc)
    pages = await db.pdf_pages.find({"pdf_id": pdf_id}, {"_id": 0}).to_list(10000)
    if pages:
        new_pages = [{**pg, "pdf_id": new_id, "owner_id": user_id} for pg in pages]
        await db.pdf_pages.insert_many(new_pages)
    await log_event("pdf.save", f"PDF condiviso importato su disco: {file_path_str}", user_id=user_id, meta={"pdf_id": new_id, "source_pdf_id": pdf_id, "path": file_path_str, "filename": p.get("filename")})
    await log_event("pdf.storage", f"Storage finale: LOCAL - path={file_path_str}", user_id=user_id, meta={"pdf_id": new_id, "storage_type": "local", "file_path": file_path_str})
    await log_event("pdf.import", f"Importato PDF condiviso: {p.get('title')}", user_id=user_id, meta={"pdf_id": new_id, "source_pdf_id": pdf_id})
    return {"ok": True, "pdf_id": new_id}


# ----------------- Admin Logs -----------------
@api.get("/admin/logs")
async def admin_logs(
    q: Optional[str] = None,
    event_type: Optional[str] = None,
    sort: str = "date_desc",
    limit: int = 200,
    _: str = Depends(require_admin),
):
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


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
