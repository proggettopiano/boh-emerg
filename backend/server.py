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
from typing import List, Optional, Dict, Any

import aiofiles
from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, UploadFile, File, Form, Query, BackgroundTasks
from fastapi.responses import Response, FileResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr, Field
import httpx

from auth_utils import (
    hash_password, verify_password, create_jwt, decode_jwt,
    get_client_ip, get_current_user_id, get_optional_user_id,
)
from pdf_processor import extract_pages, compress_pdf, make_snippet
import google_integration as gi

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

UPLOAD_DIR = ROOT_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

APP_NAME = os.environ.get("APP_NAME", "ScoreLib")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@scorelib.app").lower()
WORKER_SECRET = os.environ.get("WORKER_SECRET", "")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_indexes()
    await seed_admin()
    await migrate_single_owner()
    
    # Startup job recovery
    stuck_jobs = await db.upload_jobs.find({"status": {"$in": ["processing", "queued"]}}).to_list(1000)
    await db.upload_jobs.update_many(
        {"status": {"$in": ["processing", "queued"]}},
        {"$set": {"status": "queued", "error": "requeued_at_startup", "updated_at": iso_now()}}
    )
    for _j in stuck_jobs:
        asyncio.create_task(process_pdf_job(_j["id"]))
    yield

app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api")

logger = logging.getLogger("scorelib")
logging.basicConfig(level=logging.INFO)

# ----------------- Helpers -----------------
def iso_now(): return datetime.now(timezone.utc).isoformat()

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

async def seed_admin():
    admin = await db.users.find_one({"email": ADMIN_EMAIL})
    if not admin:
        pwd = os.environ.get("ADMIN_PASSWORD", "admin123")
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

# ----------------- Auth -----------------
def user_public(u: dict) -> dict:
    return {
        "user_id": u["user_id"],
        "email": u["email"],
        "name": u.get("name", ""),
        "is_admin": u.get("is_admin", False) or u.get("email", "").lower() == ADMIN_EMAIL,
        "created_at": u.get("created_at"),
    }

async def require_admin(user_id: str = Depends(get_current_user_id)):
    u = await db.users.find_one({"user_id": user_id}, {"is_admin": 1, "email": 1})
    if not u or (not u.get("is_admin") and u.get("email", "").lower() != ADMIN_EMAIL):
        raise HTTPException(status_code=403, detail="Solo amministratori")
    return user_id

@api.post("/auth/login")
async def login(payload: LoginIn, request: Request):
    ip = get_client_ip(request)
    email = payload.email.lower().strip()
    
    if email == ADMIN_EMAIL:
        if not payload.password:
            raise HTTPException(status_code=400, detail="Password richiesta")
        u = await db.users.find_one({"email": email})
        if not u or not verify_password(payload.password, u["password_hash"]):
            raise HTTPException(status_code=401, detail="Credenziali non valide")
        token = create_jwt(u["user_id"])
        return {"token": token, "user": user_public(u), "role": "admin"}

    if email == "chiesapomigliano@scorebil.com":
        req = await db.access_requests.find_one({"ip": ip, "status": "approved"})
        if req:
            admin = await db.users.find_one({"email": ADMIN_EMAIL}, {"user_id": 1})
            token = create_jwt(admin["user_id"])
            return {"token": token, "user": {"email": email, "name": "Gruppo Chiesa Pomigliano", "is_group": True}}
        
        rej = await db.access_requests.find_one({"ip": ip, "status": "rejected"})
        if rej: raise HTTPException(status_code=403, detail="Accesso rifiutato.")
        return {"action": "request_access", "email": email}

    raise HTTPException(status_code=404, detail="Email non riconosciuta")

@api.post("/auth/request-access")
async def request_access(payload: AccessRequestIn, request: Request):
    ip = get_client_ip(request)
    await db.access_requests.update_one(
        {"email": payload.email, "ip": ip},
        {"$set": {"name": payload.name, "status": "pending", "created_at": iso_now()}},
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
async def backup_run(user_id: str = Depends(get_current_user_id)):
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
        "status": p.get("status", "ready"),
        "is_protected": p.get("is_protected", False),
        "tags": p.get("tags", []),
        "is_favorite": p.get("is_favorite", False),
        "created_at": p.get("created_at"),
    }

@api.get("/pdfs")
async def list_pdfs(user_id: str = Depends(get_current_user_id)):
    cursor = db.pdfs.find({}, {"_id": 0}).sort("created_at", -1)
    items = await cursor.to_list(1000)
    return {"items": [_serialize_pdf(i) for i in items]}

@api.post("/pdfs/upload")
async def upload_pdf(background_tasks: BackgroundTasks, file: UploadFile = File(...), user_id: str = Depends(get_current_user_id)):
    content = await file.read()
    if len(content) > 50 * 1024 * 1024: raise HTTPException(status_code=413, detail="File troppo grande")
    
    pdf_id = f"pdf_{uuid.uuid4().hex[:12]}"
    filename = re.sub(r"[^\w\-\.]", "_", file.filename)
    fpath = UPLOAD_DIR / f"{pdf_id}_{filename}"
    fpath.write_bytes(content)
    
    await db.pdfs.insert_one({
        "id": pdf_id, "title": filename, "filename": filename, "file_path": str(fpath),
        "size": len(content), "status": "pending", "owner_id": user_id, "created_at": iso_now()
    })
    
    job_id = str(uuid.uuid4())
    await db.upload_jobs.insert_one({"id": job_id, "pdf_id": pdf_id, "status": "queued", "created_at": iso_now()})
    background_tasks.add_task(process_pdf_job, job_id)
    
    return {"results": [{"ok": True, "pdf_id": pdf_id}]}

@api.get("/pdfs/{pdf_id}/status")
async def get_pdf_status(pdf_id: str, user_id: str = Depends(get_current_user_id)):
    p = await db.pdfs.find_one({"id": pdf_id}, {"status": 1, "pages": 1})
    if not p: raise HTTPException(status_code=404, detail="Non trovato")
    return p

@api.get("/pdfs/{pdf_id}")
async def get_pdf(pdf_id: str, user_id: str = Depends(get_current_user_id)):
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    if not p: raise HTTPException(status_code=404, detail="PDF non trovato")
    return _serialize_pdf(p)

@api.patch("/pdfs/{pdf_id}")
async def patch_pdf(pdf_id: str, payload: PdfPatchIn, user_id: str = Depends(get_current_user_id)):
    update = payload.model_dump(exclude_none=True)
    if update: await db.pdfs.update_one({"id": pdf_id}, {"$set": update})
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    return _serialize_pdf(p)

@api.delete("/pdfs/{pdf_id}")
async def delete_pdf(pdf_id: str, user_id: str = Depends(require_admin)):
    p = await db.pdfs.find_one({"id": pdf_id})
    if p and os.path.exists(p["file_path"]): os.remove(p["file_path"])
    await db.pdfs.delete_one({"id": pdf_id})
    await db.pdf_pages.delete_many({"pdf_id": pdf_id})
    return {"ok": True}

@api.get("/pdfs/{pdf_id}/file")
async def get_pdf_file(pdf_id: str, token: Optional[str] = Query(None), user_id: Optional[str] = Depends(get_optional_user_id)):
    if not user_id and token: user_id = decode_jwt(token)
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    if not p: raise HTTPException(status_code=404, detail="PDF non trovato")
    if p.get("is_protected") and not user_id: raise HTTPException(status_code=401, detail="Protetto")
    fpath = Path(p["file_path"])
    if fpath.exists(): return FileResponse(fpath, media_type="application/pdf", filename=p["filename"])
    raise HTTPException(status_code=404, detail="File non trovato")

# ----------------- Search -----------------
@api.get("/search")
async def search(q: str = Query(..., min_length=1), user_id: str = Depends(get_current_user_id)):
    safe_q = re.escape(q.strip())
    results = []
    cursor = db.pdf_pages.find({"text": {"$regex": safe_q, "$options": "i"}}).limit(100)
    async for pg in cursor:
        p = await db.pdfs.find_one({"id": pg["pdf_id"]})
        if p: results.append({"pdf_id": p["id"], "title": p["title"], "page": pg["page"], "snippet": make_snippet(pg["text"], q), "is_protected": p.get("is_protected", False)})
    return {"results": results}

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

@api.get("/admin/logs")
async def admin_logs(event_type: Optional[str] = None, q: Optional[str] = None, limit: int = 100, _: str = Depends(require_admin)):
    query = {}
    if event_type: query["event_type"] = event_type
    if q: query["description"] = {"$regex": re.escape(q), "$options": "i"}
    items = await db.app_logs.find(query).sort("created_at", -1).limit(limit).to_list(limit)
    types = await db.app_logs.distinct("event_type")
    return {"items": items, "types": types}

@api.get("/admin/access-requests")
async def list_access_requests(_: str = Depends(require_admin)):
    return await db.access_requests.find({}).sort("created_at", -1).to_list(100)

@api.post("/admin/access-requests/approve")
async def approve_access(payload: dict, _: str = Depends(require_admin)):
    await db.access_requests.update_one({"email": payload["email"]}, {"$set": {"status": "approved"}})
    return {"ok": True}

@api.post("/admin/access-requests/reject")
async def reject_access(payload: dict, _: str = Depends(require_admin)):
    await db.access_requests.update_one({"email": payload["email"]}, {"$set": {"status": "rejected"}})
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
            pages_text, total, _, _ = extract_pages(fpath.read_bytes())
            for i, txt in enumerate(pages_text):
                await db.pdf_pages.update_one({"pdf_id": pdf["id"], "page": i+1}, {"$set": {"text": txt}}, upsert=True)
            await db.pdfs.update_one({"id": pdf["id"]}, {"$set": {"status": "ready", "pages": total}})
            await db.upload_jobs.update_one({"id": job_id}, {"$set": {"status": "completed"}})
    except Exception as e:
        await db.upload_jobs.update_one({"id": job_id}, {"$set": {"status": "failed", "error": str(e)}})
