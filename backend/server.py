import os
import io
import re
import uuid
import base64
import hashlib
import logging
import secrets
import asyncio
import psutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

import aiofiles
from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, UploadFile, File, Form, Query, BackgroundTasks
from fastapi.responses import Response, FileResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr, Field
import httpx
import resend

from auth_utils import (
    hash_password, verify_password, create_jwt, decode_jwt,
    get_client_ip, get_current_user_id, get_optional_user_id,
)
from pdf_processor import extract_pages, compress_pdf, make_snippet, clean_pdf_text
import google_integration as gi

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

UPLOAD_DIR = ROOT_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True, parents=True)
MAX_UPLOAD_SIZE_BYTES = int(os.environ.get("MAX_UPLOAD_SIZE_BYTES", 25 * 1024 * 1024))
MAX_UPLOAD_FILES_PER_REQUEST = int(os.environ.get("MAX_UPLOAD_FILES_PER_REQUEST", 5))
MAX_UPLOAD_QUEUE_SIZE_BYTES = int(os.environ.get("MAX_UPLOAD_QUEUE_SIZE_BYTES", 200 * 1024 * 1024))

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

APP_NAME = os.environ.get("APP_NAME", "ScoreLib")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@scorelib.app").lower()
ADMIN_RESET_PASSWORD = os.environ.get("ADMIN_LOG_PASSWORD")
WORKER_SECRET = os.environ.get("WORKER_SECRET", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", f"{APP_NAME} <no-reply@scorelib.app>")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://scorelib.vercel.app")
if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY

@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_indexes()
    await seed_admin()
    await migrate_single_owner()
    asyncio.create_task(access_request_reminder_loop())
    
    # Startup job recovery
    stuck_jobs = await db.upload_jobs.find({"status": {"$in": ["processing", "queued"]}}).to_list(1000)
    await db.upload_jobs.update_many(
        {"status": {"$in": ["processing", "queued"]}},
        {"$set": {"status": "queued", "error": "requeued_at_startup", "updated_at": iso_now()}}
    )
    for _j in stuck_jobs:
        asyncio.create_task(process_pdf_job(_j["id"]))
    yield

ENABLE_DOCS = os.environ.get("ENABLE_DOCS", "0") == "1"
app = FastAPI(
    title=APP_NAME,
    lifespan=lifespan,
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configurazione CORS robusta
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://scorelib.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com https://api.fontshare.com; connect-src 'self' https://scorelib-backend.onrender.com https://fonts.googleapis.com https://api.fontshare.com; img-src 'self' data: blob:; object-src 'none'; frame-ancestors 'none'; worker-src 'self' blob:; base-uri 'self'"
}

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        if name not in response.headers:
            response.headers[name] = value
    return response

api = APIRouter(prefix="/api")

logger = logging.getLogger("scorelib")
logging.basicConfig(level=logging.INFO)

# ----------------- Helpers -----------------
def iso_now(): return datetime.now(timezone.utc).isoformat()

def clean_doc(doc: dict) -> dict:
    """Rimuove _id o lo converte in stringa per rendere il documento JSON-safe."""
    if not doc: return doc
    if "_id" in doc: doc["_id"] = str(doc["_id"])
    return doc

async def log_event(event_type: str, description: str, user_id: Optional[str] = None, level: str = "info", meta: Optional[dict] = None):
    doc = {
        "event_type": event_type,
        "description": description,
        "user_id": user_id,
        "level": level,
        "meta": meta or {},
        "created_at": iso_now(),
    }
    await db.app_logs.insert_one(doc)
    log_func = getattr(logger, level.lower(), logger.info)
    log_func(f"[{event_type.upper()}] {description} (user={user_id})")

async def send_resend_email(to_email: str, subject: str, html: str):
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY non configurata: email non inviata a %s", to_email)
        return
    params = {
        "from": RESEND_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html,
    }
    logger.info("Invio email Resend a %s subject=%s", to_email, subject)
    try:
        if hasattr(resend, "Emails") and callable(getattr(resend.Emails, "send", None)):
            await asyncio.to_thread(resend.Emails.send, params)
            logger.info("Email inviata a %s subject=%s tramite SDK", to_email, subject)
            return
        headers = {
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post("https://api.resend.com/emails", headers=headers, json=params)
            logger.info("Resend API response status=%s body=%s", resp.status_code, resp.text)
            resp.raise_for_status()
            logger.info("Email inviata a %s subject=%s tramite HTTP fallback response=%s", to_email, subject, resp.text)
    except httpx.HTTPStatusError as exc:
        response = exc.response
        body = response.text if response is not None else "<no response>"
        status_code = response.status_code if response is not None else "?"
        logger.error("Errore invio email a %s subject=%s status=%s body=%s", to_email, subject, status_code, body)
    except Exception as exc:
        logger.exception("Errore invio email a %s subject=%s", to_email, subject)

async def send_access_request_outcome_email(email: str, status: str, name: Optional[str] = None):
    safe_name = name or email
    logger.info("send_access_request_outcome_email status=%s email=%s name=%s", status, email, safe_name)
    if status == "approved":
        subject = "Esito richiesta di accesso ScoreLib"
        html = f"""
            <h1>Richiesta approvata</h1>
            <p>Ciao {safe_name},</p>
            <p>La tua richiesta di accesso per <strong>{email}</strong> è stata approvata.</p>
            <p>Potrai effettuare il login con il tuo indirizzo email.</p>
            <p>Grazie,<br />ScoreLib</p>
        """
    else:
        subject = "Esito richiesta di accesso ScoreLib"
        html = f"""
            <h1>Richiesta rifiutata</h1>
            <p>Ciao {safe_name},</p>
            <p>La tua richiesta di accesso per <strong>{email}</strong> non è stata approvata.</p>
            <p>Se desideri riprovare, invia nuovamente la richiesta di accesso.</p>
            <p>Grazie,<br />ScoreLib</p>
        """
    await send_resend_email(email, subject, html)

async def send_access_request_reminder_email(email: str, name: Optional[str] = None):
    safe_name = name or email
    logger.info("send_access_request_reminder_email email=%s name=%s", email, safe_name)
    subject = "Ricorda: completa la tua richiesta di accesso a ScoreLib"
    html = f"""
        <h1>Richiesta ancora in attesa</h1>
        <p>Ciao {safe_name},</p>
        <p>La tua richiesta di accesso per <strong>{email}</strong> è ancora in stato di attesa.</p>
        <p>Se desideri utilizzare ScoreLib, ti preghiamo di inviare nuovamente la richiesta da <a href=\"{FRONTEND_URL}\">qui</a>.</p>
        <p>Grazie,<br />ScoreLib</p>
    """
    await send_resend_email(email, subject, html)

async def send_pending_access_request_reminders():
    threshold = datetime.now(timezone.utc) - timedelta(days=3)
    cutoff = threshold.isoformat()
    query = {
        "status": "pending",
        "created_at": {"$lte": cutoff},
        "$or": [
            {"reminder_sent_at": {"$exists": False}},
            {"reminder_sent_at": None}
        ],
    }
    logger.info("Ricerca richieste accesso pending oltre 3 giorni cutoff=%s", cutoff)
    reqs = await db.access_requests.find(query).to_list(1000)
    logger.info("Trovate %d richieste pending da ricordare", len(reqs))
    for req in reqs:
        try:
            await send_access_request_reminder_email(req["email"], req.get("name"))
            await db.access_requests.update_one(
                {"_id": req["_id"]},
                {"$set": {"reminder_sent_at": iso_now()}}
            )
            logger.info("Promemoria inviato per %s", req["email"])
        except Exception as exc:
            logger.error("Errore invio promemoria access request per %s: %s", req["email"], exc)

async def access_request_reminder_loop():
    await send_pending_access_request_reminders()
    while True:
        await asyncio.sleep(24 * 3600)
        await send_pending_access_request_reminders()

async def ensure_indexes():
    async def safe_create_index(collection, keys, **kwargs):
        try:
            await collection.create_index(keys, **kwargs)
        except Exception as e:
            if "IndexKeySpecsConflict" in str(e) or "IndexOptionsConflict" in str(e):
                try:
                    idx_name = "_".join([f"{k}_{v}" for k, v in (keys if isinstance(keys, list) else [(keys, 1)])])
                    logger.warning(f"Conflitto indice su {collection.name}.{idx_name}, tento drop/recreate.")
                    await collection.drop_index(idx_name)
                    await collection.create_index(keys, **kwargs)
                except Exception as e2:
                    logger.error(f"Impossibile ricreare indice {idx_name} su {collection.name}: {e2}")
            else:
                logger.error(f"Errore creazione indice su {collection.name}: {e}")

    await safe_create_index(db.users, "user_id", unique=True)
    await safe_create_index(db.users, "email", unique=True)
    await safe_create_index(db.pdfs, "id", unique=True)
    await safe_create_index(db.pdf_pages, [("pdf_id", 1), ("page", 1)], unique=True)
    await safe_create_index(db.pdf_pages, "text")
    await safe_create_index(db.upload_jobs, "id", unique=True)
    await safe_create_index(db.app_logs, "created_at")
    await safe_create_index(db.access_requests, "email")
    await safe_create_index(db.access_requests, "ip")
    await safe_create_index(db.shared_libraries, "id", unique=True)
    await safe_create_index(db.shared_libraries, "share_token", unique=True)

async def seed_admin():
    admin = await db.users.find_one({"email": ADMIN_EMAIL})
    if not admin:
        pwd = os.environ.get("ADMIN_PASSWORD")
        if not pwd:
            logger.error("ADMIN_PASSWORD non definita: utente admin non creato. Impostare la variabile d'ambiente e riavviare.")
            return
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        await db.users.insert_one({
            "user_id": user_id,
            "email": ADMIN_EMAIL,
            "password_hash": hash_password(pwd),
            "name": "Administrator",
            "is_admin": True,
            "created_at": iso_now(),
        })

async def migrate_single_owner():
    admin = await db.users.find_one({"email": ADMIN_EMAIL}, {"user_id": 1})
    if not admin: return
    admin_id = admin["user_id"]
    await db.pdfs.update_many({"owner_id": {"$ne": admin_id}}, {"$set": {"owner_id": admin_id}})

# ----------------- Models -----------------
class LoginIn(BaseModel):
    email: EmailStr
    password: Optional[str] = None

class AccessRequestIn(BaseModel):
    name: str
    email: EmailStr

class PdfPatchIn(BaseModel):
    title: Optional[str] = None
    is_favorite: Optional[bool] = None
    tags: Optional[List[str]] = None
    is_protected: Optional[bool] = None

class CreateLibraryIn(BaseModel):
    name: str
    description: Optional[str] = None

class AddPdfsIn(BaseModel):
    pdf_ids: List[str]

# ----------------- Auth -----------------
def user_public(u: dict) -> dict:
    is_admin = u.get("is_admin", False) or u.get("email", "").lower() == ADMIN_EMAIL
    return {
        "user_id": u["user_id"],
        "email": u["email"],
        "name": u.get("name", ""),
        "is_admin": is_admin,
        "created_at": u.get("created_at"),
        "role": "admin" if is_admin else "user",
    }

async def require_admin(user_id: str = Depends(get_current_user_id)):
    u = await db.users.find_one({"user_id": user_id})
    if not u:
        raise HTTPException(status_code=401, detail="Non autenticato")
    is_admin = u.get("is_admin", False) or u.get("email", "").lower() == ADMIN_EMAIL
    if not is_admin:
        raise HTTPException(status_code=403, detail="Solo amministratori")
    return user_id

@api.post("/auth/login")
@limiter.limit("5/minute")
async def login(payload: LoginIn, request: Request):
    ip = get_client_ip(request)
    email = payload.email.lower().strip()

    if email == ADMIN_EMAIL:
        if not payload.password:
            raise HTTPException(status_code=400, detail="Password richiesta")
        u = await db.users.find_one({"email": email})
        if not u or not verify_password(payload.password, u["password_hash"]):
            await log_event("auth.login_failed", f"Tentativo login admin fallito", level="warn", meta={"email": email, "ip": ip})
            raise HTTPException(status_code=401, detail="Credenziali non valide")
        token = create_jwt(u["user_id"])
        await log_event("auth.login_admin", f"Admin login: {email}", user_id=u["user_id"], meta={"ip": ip})
        return {"token": token, "user": user_public(u)}

    req = await db.access_requests.find_one({"email": email})
    if req and req.get("status") == "approved":
        user = await db.users.find_one({"email": email})
        if not user:
            user_id = f"user_{uuid.uuid4().hex[:12]}"
            user = {
                "user_id": user_id,
                "email": email,
                "name": req.get("name", ""),
                "is_admin": False,
                "created_at": iso_now(),
            }
            await db.users.insert_one(user)
        token = create_jwt(user["user_id"])
        await log_event("auth.login", f"Accesso utente approvato: {email}", user_id=user["user_id"], meta={"ip": ip, "email": email})
        return {"token": token, "user": user_public(user)}

    # Provide clearer messages depending on access_request state
    status = req.get("status") if req else None
    await log_event("auth.login_denied", f"Tentativo login non approvato: {email}", level="warn", meta={"email": email, "ip": ip, "status": status or "missing"})
    if status == "pending":
        raise HTTPException(status_code=403, detail="Richiesta di accesso in attesa di approvazione.")
    if status == "rejected":
        raise HTTPException(status_code=403, detail="La richiesta di accesso è stata rifiutata.")
    # no request found
    raise HTTPException(status_code=403, detail="Nessuna richiesta trovata. Richiedi l'accesso.")

@api.post("/auth/request-access")
@limiter.limit("3/hour")
async def request_access(payload: AccessRequestIn, request: Request):
    email = payload.email.lower().strip()
    ip = get_client_ip(request)
    await db.access_requests.update_one(
        {"email": email},
        {
            "$set": {"name": payload.name, "email": email, "ip": ip, "status": "pending", "created_at": iso_now()},
            "$unset": {"reminder_sent_at": ""}
        },
        upsert=True
    )
    return {"message": "Richiesta inviata."}

@api.get("/auth/me")
async def me(user_id: str = Depends(get_current_user_id)):
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not u: raise HTTPException(status_code=404, detail="Utente non trovato")
    return user_public(u)

# ----------------- Backup / Drive -----------------
async def get_master_drive():
    return await db.config.find_one({"key": "master_drive"})


async def _resolve_drive_refresh_token(pdf_record: dict) -> Optional[str]:
    """Return the best available Google Drive refresh token for a PDF backup.

    Prefer the owner token, but fall back to the master Drive token when the
    owner token is missing or invalid, which helps recover PDFs after a revoked
    refresh token.
    """
    refresh = None

    if pdf_record.get("drive_owner") == "master":
        master = await get_master_drive()
        refresh = master.get("refresh_token") if master else None
        return refresh

    owner = await db.users.find_one({"user_id": pdf_record.get("owner_id")}, {"_id": 0})
    refresh = (owner or {}).get("google_refresh_token")

    if refresh:
        return refresh

    master = await get_master_drive()
    return master.get("refresh_token") if master else None


async def _download_from_drive_with_fallback(pdf_record: dict) -> bytes:
    """Download a PDF backup from Drive, trying the owner token first and then
    master Drive as a fallback if the owner token is invalid or revoked.
    """
    primary = await _resolve_drive_refresh_token(pdf_record)
    candidates = [primary] if primary else []

    master = await get_master_drive()
    master_token = master.get("refresh_token") if master else None
    if master_token and master_token not in candidates:
        candidates.append(master_token)

    last_error = None
    for token in candidates:
        try:
            return await asyncio.to_thread(gi.download_from_drive, token, pdf_record["drive_file_id"])
        except Exception as exc:
            last_error = exc
            await log_event(
                "pdf.error",
                f"Drive download fallback failed with token source: {type(exc).__name__}: {exc}",
                user_id=pdf_record.get("owner_id"),
                level="error",
                meta={"pdf_id": pdf_record.get("id"), "drive_file_id": pdf_record.get("drive_file_id"), "stage": "drive_download_fallback"},
            )

    if last_error:
        raise last_error
    raise RuntimeError("Nessun token Drive disponibile per il recupero del PDF")

@api.get("/backup/status")
async def backup_status(user_id: str = Depends(get_current_user_id)):
    master = await get_master_drive()
    has_master = bool(master and master.get("refresh_token"))
    total = await db.pdfs.count_documents({})
    backed = await db.pdfs.count_documents({"drive_file_id": {"$nin": [None, ""]}})
    return {
        "drive_connected": has_master,
        "total_pdfs": total,
        "backed_up_pdfs": backed,
        "pending_pdfs": max(0, total - backed),
    }

@api.post("/backup/run")
async def backup_run(user_id: str = Depends(require_admin)):
    master = await get_master_drive()
    if not master: raise HTTPException(status_code=400, detail="Master Drive non connesso")
    return {"ok": True, "pending": 0}

# ----------------- PDFs -----------------
def _serialize_pdf(p: dict) -> dict:
    return {
        "id": p["id"],
        "title": p.get("title", ""),
        "filename": p.get("filename", ""),
        "size": p.get("size", 0),
        "pages": p.get("pages", 0),
        "page_labels": p.get("page_labels", []),
        "status": p.get("status", "ready"),
        "is_protected": p.get("is_protected", False),
        "tags": p.get("tags", []),
        "is_favorite": p.get("is_favorite", False),
        "created_at": p.get("created_at"),
        "owner_id": p.get("owner_id"),
    }

@api.get("/users/approved")
async def approved_users(user_id: str = Depends(get_current_user_id)):
    reqs = await db.access_requests.find({"status": "approved"}).to_list(1000)
    return {"users": [{"email": r["email"], "name": r["name"], "created_at": r["created_at"]} for r in reqs]}

# ----------------- PDFs -----------------
@api.get("/pdfs")
async def list_pdfs(
    favorite: Optional[bool] = None,
    tag: Optional[str] = None,
    sort: Optional[str] = None,
    user_id: str = Depends(get_current_user_id),
):
    query = {}
    if favorite is not None:
        query["is_favorite"] = favorite
    if tag:
        query["tags"] = tag.lower()
    sort_mapping = {
        "date_asc": [("created_at", 1)],
        "date_desc": [("created_at", -1)],
        "name_asc": [("title", 1)],
        "name_desc": [("title", -1)],
    }
    order = sort_mapping.get(sort, [("created_at", -1)])
    cursor = db.pdfs.find(query, {"_id": 0}).sort(order)
    items = await cursor.to_list(1000)
    return {"items": [_serialize_pdf(i) for i in items]}

@api.post("/pdfs/upload")
async def upload_pdf(
    files: List[UploadFile] = File(...),
    background_tasks: BackgroundTasks = None,
    user_id: str = Depends(get_current_user_id)
):
    if not files:
        raise HTTPException(status_code=400, detail="Nessun file inviato")

    user = await db.users.find_one({"user_id": user_id})
    is_admin = user and (user.get("is_admin") or user.get("email", "").lower() == ADMIN_EMAIL)
    if not is_admin and len(files) > MAX_UPLOAD_FILES_PER_REQUEST:
        raise HTTPException(status_code=413, detail=f"Solo {MAX_UPLOAD_FILES_PER_REQUEST} file possono essere caricati per volta")

    results = []
    total_uploaded_size = 0
    for file in files:
        content = await file.read()
        total_uploaded_size += len(content)
        if not is_admin and total_uploaded_size > MAX_UPLOAD_QUEUE_SIZE_BYTES:
            raise HTTPException(status_code=413, detail=f"Superata la dimensione massima totale di {MAX_UPLOAD_QUEUE_SIZE_BYTES // (1024 * 1024)} MB per upload")
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(status_code=413, detail=f"File troppo grande: massimo {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB")

        pdf_bytes, was_compressed = compress_pdf(content)
        pdf_id = f"pdf_{uuid.uuid4().hex[:12]}"
        filename = re.sub(r"[^\w\-\.]", "_", file.filename)
        fpath = UPLOAD_DIR / f"{pdf_id}_{filename}"
        fpath.write_bytes(pdf_bytes)

        await db.pdfs.insert_one({
            "id": pdf_id,
            "title": filename,
            "filename": filename,
            "file_path": str(fpath),
            "size": len(pdf_bytes),
            "status": "pending",
            "owner_id": user_id,
            "compressed": was_compressed,
            "storage_type": "local",
            "drive_owner": None,
            "drive_file_id": None,
            "synced_at": None,
            "created_at": iso_now(),
        })

        job_id = str(uuid.uuid4())
        await db.upload_jobs.insert_one({
            "id": job_id,
            "pdf_id": pdf_id,
            "status": "queued",
            "created_at": iso_now()
        })
        background_tasks.add_task(process_pdf_job, job_id)

        results.append({"ok": True, "pdf_id": pdf_id, "name": filename, "compressed": was_compressed})

    if results:
        await log_event("pdf.uploaded", f"Upload completato: {len(results)} file", user_id=user_id, meta={"count": len(results)})
    
    return {"results": results}

@api.get("/pdfs/{pdf_id}/status")
async def get_pdf_status(pdf_id: str, user_id: str = Depends(get_current_user_id)):
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0, "status": 1, "pages": 1})
    if not p: raise HTTPException(status_code=404, detail="Non trovato")
    # Check access
    can_access = await _user_can_access_pdf(user_id, pdf_id)
    if not can_access: raise HTTPException(status_code=403, detail="Accesso negato")
    return p

@api.get("/pdfs/{pdf_id}")
async def get_pdf(pdf_id: str, user_id: Optional[str] = Depends(get_optional_user_id), share_token: Optional[str] = Query(None)):
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="PDF non trovato")

    can_access = await _user_can_access_pdf(user_id, pdf_id, share_token)
    if not can_access:
        if p.get("is_protected") and not user_id:
            raise HTTPException(status_code=401, detail="Protetto")
        raise HTTPException(status_code=403, detail="Accesso negato")

    return _serialize_pdf(p)

@api.patch("/pdfs/{pdf_id}")
async def patch_pdf(pdf_id: str, payload: PdfPatchIn, user_id: str = Depends(get_current_user_id)):
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    if not p: raise HTTPException(status_code=404, detail="PDF non trovato")
    can_access = await _user_can_access_pdf(user_id, pdf_id)
    if not can_access: raise HTTPException(status_code=403, detail="Accesso negato")
    u = await db.users.find_one({"user_id": user_id})
    is_admin = u and (u.get("is_admin") or u.get("email", "").lower() == ADMIN_EMAIL)
    update = payload.model_dump(exclude_none=True)
    # protected PDFs: only allow tags and is_favorite
    if p.get("is_protected") and not is_admin:
        restricted_keys = {"title", "is_protected"}
        if any(key in update for key in restricted_keys):
            raise HTTPException(status_code=403, detail="Operazione non consentita su file protetto")
    if update.get("is_protected") and not is_admin:
        raise HTTPException(status_code=403, detail="Solo un amministratore può modificare lo stato protetto")
    if any(key in update for key in ["title"]):
        if not is_admin and p.get("owner_id") != user_id:
            raise HTTPException(status_code=403, detail="Solo il proprietario o un amministratore possono modificare questo file")
    if update:
        await db.pdfs.update_one({"id": pdf_id}, {"$set": update})
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    return _serialize_pdf(p)

@api.delete("/pdfs/{pdf_id}")
async def delete_pdf(pdf_id: str, user_id: str = Depends(get_current_user_id)):
    p = await db.pdfs.find_one({"id": pdf_id})
    if not p: raise HTTPException(status_code=404, detail="PDF non trovato")
    u = await db.users.find_one({"user_id": user_id})
    is_admin = u and (u.get("is_admin") or u.get("email", "").lower() == ADMIN_EMAIL)
    if p.get("is_protected") and not is_admin:
        raise HTTPException(status_code=403, detail="Operazione non consentita su file protetto")
    if p and os.path.exists(p["file_path"]):
        try:
            os.remove(p["file_path"])
        except Exception:
            pass
    await db.pdfs.delete_one({"id": pdf_id})
    await db.pdf_pages.delete_many({"pdf_id": pdf_id})
    await db.shared_libraries.update_many({}, {"$pull": {"pdf_ids": pdf_id}})
    await log_event("pdf.deleted", f"PDF eliminato: {pdf_id}", user_id=user_id, meta={"pdf_id": pdf_id})
    return {"ok": True}

@api.get("/pdfs/{pdf_id}/file")
async def get_pdf_file(pdf_id: str, user_id: Optional[str] = Depends(get_optional_user_id), share_token: Optional[str] = Query(None)):
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="PDF non trovato")

    can_access = await _user_can_access_pdf(user_id, pdf_id, share_token)
    if not can_access:
        if p.get("is_protected") and not user_id:
            raise HTTPException(status_code=401, detail="Protetto")
        raise HTTPException(status_code=403, detail="Accesso negato")
    # Diagnostic logging: capture metadata and whether local file exists
    file_path = p.get("file_path")
    try:
        fpath = Path(file_path) if file_path else Path("")
        file_exists = fpath.exists()
    except Exception:
        fpath = Path("")
        file_exists = False

    await log_event(
        "pdf.debug",
        "PDF_DEBUG",
        user_id=user_id,
        meta={
            "pdf_id": pdf_id,
            "file_path": str(file_path),
            "file_exists": file_exists,
            "drive_file_id": p.get("drive_file_id"),
            "drive_owner": p.get("drive_owner"),
            "storage_type": p.get("storage_type"),
        },
    )

    if file_exists:
        await log_event(
            "pdf.debug",
            "PDF_SERVE_LOCAL",
            user_id=user_id,
            meta={"pdf_id": pdf_id, "file_path": str(fpath), "filename": p.get("filename")},
        )
        return FileResponse(fpath, media_type="application/pdf", filename=p["filename"])

    # Local file missing — attempt Drive fallback if available
    await log_event(
        "pdf.file_missing",
        "PDF locale mancante, provo fallback Drive",
        user_id=user_id,
        meta={"pdf_id": pdf_id, "file_path": str(file_path), "drive_file_id": p.get("drive_file_id"), "drive_owner": p.get("drive_owner")},
    )

    if p.get("drive_file_id"):
        try:
            data = await _download_from_drive_with_fallback(p)
            new_path = UPLOAD_DIR / Path(file_path).name
            new_path.parent.mkdir(parents=True, exist_ok=True)
            new_path.write_bytes(data)
            await db.pdfs.update_one({"id": pdf_id}, {"$set": {
                "file_path": str(new_path),
                "storage_type": "local",
                "synced_at": iso_now(),
            }})
            await log_event(
                "pdf.drive_restore",
                "PDF ripristinato da Drive",
                user_id=user_id,
                meta={"pdf_id": pdf_id, "drive_file_id": p["drive_file_id"], "file_path": str(new_path)},
            )
            return FileResponse(new_path, media_type="application/pdf", filename=p["filename"])
        except Exception as e:
                await log_event(
                    "pdf.debug",
                    "PDF_DRIVE_FALLBACK_ERROR",
                    user_id=user_id,
                    level="error",
                    meta={
                        "pdf_id": pdf_id,
                        "drive_file_id": p.get("drive_file_id"),
                        "exception_repr": repr(e),
                        "exception_str": str(e),
                        "stage": "get_pdf_file_fallback",
                    },
                )
                await log_event("pdf.error", f"Drive download fallito: {e}", user_id=user_id, level="error", meta={"pdf_id": pdf_id, "drive_file_id": p.get("drive_file_id"), "stage": "get_pdf_file_fallback"})

    raise HTTPException(status_code=404, detail="File non trovato")

@api.post("/pdfs/{pdf_id}/reload")
async def reload_pdf(pdf_id: str, user_id: str = Depends(get_current_user_id)):
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="PDF non trovato")
    if p.get("is_protected") and not user_id:
        raise HTTPException(status_code=401, detail="Protetto")
    can_access = await _user_can_access_pdf(user_id, pdf_id)
    if not can_access:
        raise HTTPException(status_code=403, detail="Accesso negato")
    drive_file_id = p.get("drive_file_id")
    if not drive_file_id:
        raise HTTPException(status_code=404, detail="Backup Drive non disponibile")

    try:
        data = await _download_from_drive_with_fallback(p)
        new_path = UPLOAD_DIR / Path(p["file_path"]).name
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_bytes(data)
        await db.pdfs.update_one({"id": pdf_id}, {"$set": {
            "file_path": str(new_path),
            "storage_type": "local",
            "synced_at": iso_now(),
        }})
        await log_event("pdf.drive_restore", "PDF ripristinato da Drive via reload endpoint", user_id=user_id, meta={"pdf_id": pdf_id, "drive_file_id": drive_file_id, "file_path": str(new_path)})
        return {"ok": True}
    except Exception as e:
        await log_event("pdf.error", f"Drive download reload fallito: {e}", user_id=user_id, level="error", meta={"pdf_id": pdf_id, "drive_file_id": drive_file_id, "stage": "reload"})
        raise HTTPException(status_code=502, detail="Recupero da Drive fallito")

# ----------------- Libraries -----------------
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
        "public": True,
        "created_at": iso_now(),
    }
    await db.shared_libraries.insert_one(doc)
    await log_event("library.create", f"Libreria creata: {doc['name']}", user_id=user_id)
    return clean_doc(doc)

@api.get("/libraries")
async def list_libraries(user_id: str = Depends(get_current_user_id)):
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    is_admin = bool(u and (u.get("is_admin") or u.get("email", "").lower() == ADMIN_EMAIL))

    query = {"hidden_by_users": {"$ne": user_id}}
    if not is_admin:
        query["$or"] = [{"owner_id": user_id}, {"members": user_id}]

    cursor = db.shared_libraries.find(query, {"_id": 0}).sort("created_at", -1)
    items = await cursor.to_list(1000)
    return {"items": items}

@api.get("/libraries/hidden")
async def list_hidden_libraries(user_id: str = Depends(get_current_user_id)):
    cursor = db.shared_libraries.find({"hidden_by_users": user_id}, {"_id": 0}).sort("created_at", -1)
    items = await cursor.to_list(1000)
    return {"items": items}

@api.get("/libraries/{lib_id}")
async def get_library(lib_id: str, user_id: str = Depends(get_current_user_id)):
    lib = await db.shared_libraries.find_one({"id": lib_id}, {"_id": 0})
    if not lib: raise HTTPException(status_code=404, detail="Libreria non trovata")
    pdfs = await db.pdfs.find({"id": {"$in": lib.get("pdf_ids", [])}}, {"_id": 0}).to_list(1000)
    lib["pdfs"] = [_serialize_pdf(p) for p in pdfs]
    lib["is_owner"] = lib["owner_id"] == user_id
    return lib

@api.post("/libraries/{lib_id}/pdfs")
async def add_to_library(lib_id: str, payload: AddPdfsIn, user_id: str = Depends(get_current_user_id)):
    lib = await db.shared_libraries.find_one({"id": lib_id})
    if not lib: raise HTTPException(status_code=404, detail="Libreria non trovata")

    requester = await db.users.find_one({"user_id": user_id})
    is_admin = bool(requester and (requester.get("is_admin") or requester.get("email", "").lower() == ADMIN_EMAIL))

    added = []
    protected = []
    skipped = []
    existing_ids = set(lib.get("pdf_ids", []))

    for pdf_id in payload.pdf_ids:
        if pdf_id in existing_ids:
            skipped.append(pdf_id)
            continue
        p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0, "is_protected": 1, "owner_id": 1})
        if not p:
            skipped.append(pdf_id)
            continue
        can_add_protected = is_admin or p.get("owner_id") == user_id
        if p.get("is_protected") and not can_add_protected:
            protected.append(pdf_id)
            continue
        added.append(pdf_id)
        existing_ids.add(pdf_id)

    if added:
        await db.shared_libraries.update_one({"id": lib_id}, {"$addToSet": {"pdf_ids": {"$each": added}}})

    return {"added": added, "protected": protected, "skipped": skipped}

@api.delete("/libraries/{lib_id}/pdfs/{pdf_id}")
async def remove_from_library(lib_id: str, pdf_id: str, user_id: str = Depends(get_current_user_id)):
    lib = await db.shared_libraries.find_one({"id": lib_id})
    if not lib: raise HTTPException(status_code=404, detail="Libreria non trovata")
    u = await db.users.find_one({"user_id": user_id})
    is_admin = u and (u.get("is_admin") or u.get("email", "").lower() == ADMIN_EMAIL)
    is_member = user_id in lib.get("members", [])
    if not is_admin and lib.get("owner_id") != user_id and not is_member:
        raise HTTPException(status_code=403, detail="Solo il proprietario, un amministratore o un membro possono modificare questa libreria")
    await db.shared_libraries.update_one({"id": lib_id}, {"$pull": {"pdf_ids": pdf_id}})
    return {"ok": True}

@api.delete("/libraries/{lib_id}")
async def delete_library(lib_id: str, user_id: str = Depends(get_current_user_id)):
    lib = await db.shared_libraries.find_one({"id": lib_id})
    if not lib: raise HTTPException(status_code=404, detail="Libreria non trovata")
    u = await db.users.find_one({"user_id": user_id})
    is_admin = u and (u.get("is_admin") or u.get("email", "").lower() == ADMIN_EMAIL)
    if not is_admin and lib.get("owner_id") != user_id:
        raise HTTPException(status_code=403, detail="Solo il proprietario o un amministratore possono eliminare questa libreria")
    await db.shared_libraries.delete_one({"id": lib_id})
    return {"ok": True}

@api.post("/libraries/{lib_id}/hide")
async def hide_library(lib_id: str, user_id: str = Depends(get_current_user_id)):
    lib = await db.shared_libraries.find_one({"id": lib_id}, {"_id": 0})
    if not lib:
        raise HTTPException(status_code=404, detail="Libreria non trovata")
    if lib.get("owner_id") == user_id:
        raise HTTPException(status_code=403, detail="Il proprietario non può nascondere la propria libreria")
    if user_id not in lib.get("members", []):
        raise HTTPException(status_code=403, detail="Solo i membri possono nascondere questa libreria")
    await db.shared_libraries.update_one({"id": lib_id}, {"$addToSet": {"hidden_by_users": user_id}})
    return {"ok": True}

@api.post("/libraries/{lib_id}/leave")
async def leave_library(lib_id: str, user_id: str = Depends(get_current_user_id)):
    return await hide_library(lib_id, user_id)

@api.delete("/libraries/{lib_id}/hide")
async def unhide_library(lib_id: str, user_id: str = Depends(get_current_user_id)):
    lib = await db.shared_libraries.find_one({"id": lib_id}, {"_id": 0})
    if not lib:
        raise HTTPException(status_code=404, detail="Libreria non trovata")
    await db.shared_libraries.update_one({"id": lib_id}, {"$pull": {"hidden_by_users": user_id}})
    return {"ok": True}

@api.delete("/libraries/{lib_id}/leave")
async def restore_library(lib_id: str, user_id: str = Depends(get_current_user_id)):
    return await unhide_library(lib_id, user_id)

# ----------------- Shared -----------------
@api.get("/shared/{token}")
async def view_shared(token: str, user_id: Optional[str] = Depends(get_optional_user_id)):
    # 1. Try as library share token
    lib = await db.shared_libraries.find_one({"share_token": token}, {"_id": 0})
    if not lib:
        raise HTTPException(status_code=404, detail="Link non valido o rimosso")
    if lib.get("is_protected") and not user_id:
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
        try:
            data = await _download_from_drive_with_fallback(p)
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


@api.post("/pdfs/{pdf_id}/share")
async def share_pdf(pdf_id: str, user_id: str = Depends(get_current_user_id)):
    """Create a simple one-off shared link for a single PDF."""
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="PDF non trovato")
    # Only owner or admin can create share
    u = await db.users.find_one({"user_id": user_id})
    is_admin = u and (u.get("is_admin") or u.get("email", "").lower() == ADMIN_EMAIL)
    if not is_admin and p.get("owner_id") != user_id:
        raise HTTPException(status_code=403, detail="Solo il proprietario o un amministratore possono condividere questo file")

    share_id = str(uuid.uuid4())
    share_token = secrets.token_urlsafe(16)
    doc = {
        "id": share_id,
        "name": f"Condivisione - {p.get('title', p.get('filename', pdf_id))}",
        "description": "Condivisione temporanea",
        "owner_id": user_id,
        "pdf_ids": [pdf_id],
        "share_token": share_token,
        "public": True,
        "created_at": iso_now(),
    }
    await db.shared_libraries.insert_one(doc)
    await log_event("pdf.share", f"PDF condiviso: {pdf_id}", user_id=user_id, meta={"pdf_id": pdf_id, "share_token": share_token})
    return {"ok": True, "share_token": share_token, "share_url": f"/shared/{share_token}"}


async def _user_can_access_pdf(user_id: Optional[str], pdf_id: str, share_token: Optional[str] = None) -> bool:
    """
    Access rules:
    - admin sees everything
    - approved users see everything
    - shared-link viewers may access the PDF if the share token belongs to that PDF
    - otherwise no access
    """
    if user_id:
        u = await db.users.find_one({"user_id": user_id})
        if not u:
            return False

        if u.get("is_admin", False) or u.get("email", "").lower() == ADMIN_EMAIL:
            return True

        approved = await db.access_requests.find_one({"email": u.get("email", "").lower(), "status": "approved"})
        if approved:
            return True

    if share_token:
        lib = await db.shared_libraries.find_one({"share_token": share_token, "pdf_ids": pdf_id}, {"_id": 0})
        if lib:
            return True

    return False

# ----------------- Search -----------------

def format_search_result(p: dict, pg: dict, q: str, score: int, snippet: Optional[str] = None) -> dict:
    return {
        "pdf_id": p["id"],
        "title": p["title"],
        # `page` is the physical (file) page number 1-based — keep for backward compatibility
        "page": pg["page"],
        # `actual_page` mirrors `page` (some frontend code uses this name)
        "actual_page": pg["page"],
        # `viewer_page` is the canonical numeric page the viewer should open (physical page)
        "viewer_page": pg["page"],
        "page_label": pg.get("page_label", pg["page"]),
        "snippet": snippet if snippet is not None else make_snippet(clean_pdf_text(pg.get("text", "")), q),
        "score": score,
        "is_protected": p.get("is_protected", False),
    }

@api.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    pdf_ids: Optional[str] = Query(None),
    share_token: Optional[str] = Query(None),
    user_id: Optional[str] = Depends(get_optional_user_id),
):
    raw_q = clean_pdf_text(q).strip()
    if not raw_q:
        return {"results": []}

    if not user_id and not share_token:
        raise HTTPException(status_code=401, detail="Login richiesto")

    pdf_ids_list = [pid.strip() for pid in (pdf_ids or "").split(",") if pid.strip()] or None
    if share_token:
        lib = await db.shared_libraries.find_one({"share_token": share_token}, {"_id": 0, "pdf_ids": 1})
        if not lib:
            raise HTTPException(status_code=404, detail="Link non valido o rimosso")
        allowed_pdf_ids = set(lib.get("pdf_ids", []))
        if pdf_ids_list:
            pdf_ids_list = [pid for pid in pdf_ids_list if pid in allowed_pdf_ids]
        else:
            pdf_ids_list = list(allowed_pdf_ids)

    results = []
    seen = set()  # Per evitare duplicati (stessa pagina trovata con logiche diverse)

    if raw_q.isdigit():
        # 1. CERCA INIZIO INNO (PIÙ FORTE)
        hymn_regex = rf"(?m)^\s*{re.escape(raw_q)}[.\s]"
        hymn_filter = {"text": {"$regex": hymn_regex, "$options": "m"}}
        if pdf_ids_list:
            hymn_filter["pdf_id"] = {"$in": pdf_ids_list}
        cursor = db.pdf_pages.find(hymn_filter)
        async for pg in cursor:
            key = (pg["pdf_id"], pg["page"])
            if key in seen:
                continue
            seen.add(key)
            p = await db.pdfs.find_one({"id": pg["pdf_id"]})
            if p:
                results.append(format_search_result(p, pg, raw_q, score=100))

        # 2. CERCA ETICHETTA PAGINA (DEBOLE)
        label_filter = {"page_label": raw_q}
        if pdf_ids_list:
            label_filter["pdf_id"] = {"$in": pdf_ids_list}
        label_cursor = db.pdf_pages.find(label_filter)
        async for pg in label_cursor:
            key = (pg["pdf_id"], pg["page"])
            if key in seen:
                continue
            seen.add(key)
            p = await db.pdfs.find_one({"id": pg["pdf_id"]})
            if p:
                results.append(format_search_result(p, pg, raw_q, score=50, snippet=f"Pagina {raw_q}"))

    safe_q = rf"(?<!\d){re.escape(raw_q)}(?!\d)" if raw_q.isdigit() else re.escape(raw_q)
    text_filter = {"text": {"$regex": safe_q, "$options": "i"}}
    if pdf_ids_list:
        text_filter["pdf_id"] = {"$in": pdf_ids_list}
    text_cursor = db.pdf_pages.find(text_filter).limit(50)
    async for pg in text_cursor:
        key = (pg["pdf_id"], pg["page"])
        if key in seen:
            continue
        seen.add(key)
        p = await db.pdfs.find_one({"id": pg["pdf_id"]})
        if p:
            results.append(format_search_result(p, pg, raw_q, score=10))

    # Sort by score desc, then by physical page number (use actual_page if present, fall back to page)
    results.sort(key=lambda x: (-x["score"], x.get("actual_page", x.get("page", 0))))
    return {"results": results}

# ----------------- Admin Logs -----------------
@api.get("/admin/logs")
async def get_admin_logs(event_type: str = Query("all"), q: str = Query(""), sort: str = Query("date_desc"), limit: int = Query(500), user_id: str = Depends(get_current_user_id)):
    u = await db.users.find_one({"user_id": user_id})
    is_admin = u.get("is_admin") or u.get("email", "").lower() == ADMIN_EMAIL
    if not is_admin:
        raise HTTPException(status_code=403, detail="Accesso non autorizzato")
    
    query = {}
    if event_type != "all":
        query["event_type"] = event_type
    if q:
        query["description"] = {"$regex": q, "$options": "i"}
    
    sort_dir = -1 if "desc" in sort.lower() else 1
    cursor = db.app_logs.find(query, {"_id": 0}).sort("created_at", sort_dir).limit(limit)
    items = await cursor.to_list(limit)
    
    all_types = await db.app_logs.distinct("event_type")
    
    return {"items": items, "types": sorted(all_types or [])}


# ----------------- Admin -----------------
@api.get("/admin/stats")
async def admin_stats(_: str = Depends(require_admin)):
    return {
        "users_total": await db.access_requests.count_documents({"status": "approved"}),
        "pdfs_total": await db.pdfs.count_documents({}),
    }

@api.get("/admin/users")
async def admin_users(_: str = Depends(require_admin)):
    reqs = await db.access_requests.find({"status": "approved"}).to_list(1000)
    return {"users": [{"email": r["email"], "name": r["name"], "created_at": r["created_at"]} for r in reqs]}

@api.get("/admin/access-requests")
async def list_access_requests(_: str = Depends(require_admin)):
    reqs = await db.access_requests.find({}).sort("created_at", -1).to_list(100)
    return [clean_doc(r) for r in reqs]

@api.post("/admin/access-requests/approve")
async def approve_access(payload: dict, user_id: str = Depends(require_admin)):
    email = payload["email"].lower().strip()
    req = await db.access_requests.find_one({"email": email})
    await db.access_requests.update_one({"email": email}, {"$set": {"status": "approved", "email": email}})
    await log_event("access.approved", f"Richiesta accesso approvata: {email}", user_id=user_id, meta={"email": email})
    asyncio.create_task(send_access_request_outcome_email(email, "approved", req.get("name") if req else None))
    return {"ok": True}

@api.post("/admin/access-requests/reject")
async def reject_access(payload: dict, user_id: str = Depends(require_admin)):
    email = payload["email"].lower().strip()
    req = await db.access_requests.find_one({"email": email})
    await db.access_requests.update_one({"email": email}, {"$set": {"status": "rejected", "email": email}})
    await log_event("access.rejected", f"Richiesta accesso rifiutata: {email}", user_id=user_id, meta={"email": email})
    asyncio.create_task(send_access_request_outcome_email(email, "rejected", req.get("name") if req else None))
    return {"ok": True}

@api.get("/admin/master-drive/status")
async def master_drive_status(_: str = Depends(require_admin)):
    m = await get_master_drive()
    return {"connected": bool(m), "email": m.get("email", "") if m else ""}

@api.post("/admin/master-drive/url")
async def master_drive_url(payload: dict, _: str = Depends(require_admin)):
    return {"url": gi.build_auth_url(payload["redirect_uri"], secrets.token_urlsafe(16))}

@api.post("/admin/master-drive/connect")
async def master_drive_connect(payload: dict, _: str = Depends(require_admin)):
    tokens = await gi.exchange_code(payload["code"], payload["redirect_uri"])
    info = await gi.fetch_userinfo(tokens["access_token"])
    root = await asyncio.to_thread(gi.ensure_master_root, tokens["refresh_token"])
    await db.config.update_one({"key": "master_drive"}, {"$set": {"refresh_token": tokens["refresh_token"], "email": info["email"], "folder_root_id": root, "updated_at": iso_now()}}, upsert=True)
    return {"connected": True, "email": info["email"]}

@api.post("/admin/master-drive/disconnect")
async def master_drive_disconnect(_: str = Depends(require_admin)):
    await db.config.delete_one({"key": "master_drive"})
    return {"ok": True}

@api.post("/admin/reset-today")
async def reset_today_data(payload: dict, user_id: str = Depends(require_admin)):
    if not ADMIN_RESET_PASSWORD:
        raise HTTPException(status_code=503, detail="Funzione non configurata: impostare ADMIN_LOG_PASSWORD")
    provided = (payload.get("password") or "").strip()
    if not secrets.compare_digest(provided, ADMIN_RESET_PASSWORD):
        raise HTTPException(status_code=403, detail="Password non valida")

    access_deleted = await db.access_requests.delete_many({})
    users_deleted = await db.users.delete_many({"is_admin": {"$ne": True}})
    logs_deleted = await db.app_logs.delete_many({})

    await log_event(
        "admin.reset_today",
        "Reset dati amministrazione richiesto dall'amministratore",
        user_id=user_id,
        level="warn",
        meta={
            "access_requests_deleted": access_deleted.deleted_count,
            "users_deleted": users_deleted.deleted_count,
            "logs_deleted": logs_deleted.deleted_count,
        },
    )

    return {
        "ok": True,
        "deleted": {
            "access_requests": access_deleted.deleted_count,
            "users": users_deleted.deleted_count,
            "logs": logs_deleted.deleted_count,
        },
    }

# Serve manifest.json
@app.get("/manifest.json")
async def get_manifest():
    return {
        "name": APP_NAME,
        "short_name": APP_NAME,
        "description": f"{APP_NAME} - Share and manage PDF scores",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#000000",
        "icons": [
            {"src": "/icon.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    }

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok"}

app.include_router(api)

# ----------------- Worker -----------------
async def process_pdf_job(job_id):
    job = await db.upload_jobs.find_one({"id": job_id})
    if not job: return
    await db.upload_jobs.update_one({"id": job_id}, {"$set": {"status": "processing"}})
    try:
        pdf = await db.pdfs.find_one({"id": job["pdf_id"]})
        fpath = Path(pdf["file_path"])
        if fpath.exists():
            pages_text, total, _, page_labels = extract_pages(fpath.read_bytes())
            for i, txt in enumerate(pages_text):
                await db.pdf_pages.update_one(
                    {"pdf_id": pdf["id"], "page": i+1},
                    {"$set": {"text": txt, "page_label": page_labels[i]}},
                    upsert=True,
                )
            await db.pdfs.update_one({"id": pdf["id"]}, {"$set": {"status": "ready", "pages": total, "page_labels": page_labels}})
            # Backup to master Drive if configured and not already synced
            master = await get_master_drive()
            if master and master.get("refresh_token") and not pdf.get("drive_file_id"):
                try:
                    folder_id = await asyncio.to_thread(gi.ensure_master_root, master["refresh_token"])
                    drive_id = await asyncio.to_thread(gi.upload_to_drive, master["refresh_token"], folder_id, pdf["filename"], fpath.read_bytes())
                    synced_at = iso_now()
                    await db.pdfs.update_one({"id": pdf["id"]}, {"$set": {
                        "drive_file_id": drive_id,
                        "drive_owner": "master",
                        "storage_type": "drive",
                        "synced_at": synced_at,
                    }})
                    await log_event("pdf.drive_backup", f"PDF caricato su Drive master: {pdf['id']}", user_id=pdf.get("owner_id"), meta={"pdf_id": pdf["id"], "drive_file_id": drive_id, "folder_id": folder_id})
                except Exception as e:
                    await db.pdfs.update_one({"id": pdf["id"]}, {"$set": {"drive_backup_error": str(e)}})
                    await log_event("pdf.drive_error", f"Drive backup fallito: {e}", user_id=pdf.get("owner_id"), level="error", meta={"pdf_id": pdf["id"], "stage": "drive_backup"})
            await db.upload_jobs.update_one({"id": job_id}, {"$set": {"status": "completed"}})
    except Exception as e:
        await db.upload_jobs.update_one({"id": job_id}, {"$set": {"status": "failed", "error": str(e)}})
