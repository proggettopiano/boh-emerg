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
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

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
UPLOAD_DIR.mkdir(exist_ok=True, parents=True)
MAX_UPLOAD_SIZE_BYTES = int(os.environ.get("MAX_UPLOAD_SIZE_BYTES", 50 * 1024 * 1024))

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

# Configurazione CORS robusta
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

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
    await safe_create_index(db.pdfs, "group_id")
    await safe_create_index(db.pdf_pages, [("pdf_id", 1), ("page", 1)], unique=True)
    await safe_create_index(db.pdf_pages, "text")
    await safe_create_index(db.upload_jobs, "id", unique=True)
    await safe_create_index(db.app_logs, "created_at")
    await safe_create_index(db.access_requests, "email")
    await safe_create_index(db.access_requests, "ip")
    await safe_create_index(db.shared_libraries, "id", unique=True)
    await safe_create_index(db.shared_libraries, "share_token", unique=True)

    await safe_create_index(db.groups, "id", unique=True)
    await safe_create_index(db.groups, [("members", 1)])

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

class CreateLibraryIn(BaseModel):
    name: str
    description: Optional[str] = None

class AddPdfsIn(BaseModel):
    pdf_ids: List[str]

class CreateGroupIn(BaseModel):
    name: str

class AddGroupMemberIn(BaseModel):
    member_id: str

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
    u = await db.users.find_one({"user_id": user_id})
    if not u:
        raise HTTPException(status_code=401, detail="Non autenticato")
    is_admin = u.get("is_admin", False) or u.get("email", "").lower() == ADMIN_EMAIL
    if not is_admin:
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
        "status": p.get("status", "ready"),
        "is_protected": p.get("is_protected", False),
        "tags": p.get("tags", []),
        "is_favorite": p.get("is_favorite", False),
        "created_at": p.get("created_at"),
    }

# ----------------- Groups -----------------
@api.post("/groups")
async def create_group(payload: CreateGroupIn, user_id: str = Depends(get_current_user_id)):
    group_id = str(uuid.uuid4())
    doc = {
        "id": group_id,
        "name": payload.name.strip() or "Gruppo",
        "owner_id": user_id,
        "members": [user_id],
        "created_at": iso_now(),
    }
    await db.groups.insert_one(doc)
    await log_event("group.create", f"Gruppo creato: {doc['name']}", user_id=user_id, meta={"group_id": group_id})
    doc.pop("_id", None)
    return doc

@api.get("/groups")
async def list_groups(user_id: str = Depends(get_current_user_id)):
    cursor = db.groups.find(
        {"$or": [{"owner_id": user_id}, {"members": user_id}]},
        {"_id": 0}
    ).sort("created_at", -1)
    items = await cursor.to_list(1000)
    return {"items": items}

@api.post("/groups/{group_id}/members")
async def add_group_member(group_id: str, payload: AddGroupMemberIn, user_id: str = Depends(get_current_user_id)):
    grp = await db.groups.find_one({"id": group_id, "owner_id": user_id}, {"_id": 0})
    if not grp:
        raise HTTPException(status_code=403, detail="Solo il proprietario può aggiungere membri")
    await db.groups.update_one({"id": group_id}, {"$addToSet": {"members": payload.member_id}})
    await log_event("group.member_added", f"Membro aggiunto al gruppo", user_id=user_id, meta={"group_id": group_id, "member_id": payload.member_id})
    return {"ok": True}

# ----------------- PDFs -----------------
@api.get("/pdfs")
async def list_pdfs(user_id: str = Depends(get_current_user_id)):
    cursor = db.pdfs.find({}, {"_id": 0}).sort("created_at", -1)
    items = await cursor.to_list(1000)
    return {"items": [_serialize_pdf(i) for i in items]}

@api.post("/pdfs/upload")
async def upload_pdf(
    files: List[UploadFile] = File(...),
    group_id: str = Query(...),
    background_tasks: BackgroundTasks = None,
    user_id: str = Depends(get_current_user_id)
):
    # Verify user is member of group
    grp = await db.groups.find_one({"id": group_id, "members": user_id}, {"_id": 0, "id": 1})
    if not grp:
        raise HTTPException(status_code=403, detail="Non sei membro di questo gruppo")
    
    if not files:
        raise HTTPException(status_code=400, detail="Nessun file inviato")

    results = []
    for file in files:
        content = await file.read()
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
            "group_id": group_id,
            "owner_id": user_id,
            "compressed": was_compressed,
            "created_at": iso_now(),
        })

        job_id = str(uuid.uuid4())
        await db.upload_jobs.insert_one({
            "id": job_id,
            "pdf_id": pdf_id,
            "group_id": group_id,
            "status": "queued",
            "created_at": iso_now()
        })
        background_tasks.add_task(process_pdf_job, job_id)

        results.append({"ok": True, "pdf_id": pdf_id, "name": filename, "compressed": was_compressed})

    return {"results": results}

@api.get("/pdfs/{pdf_id}/status")
async def get_pdf_status(pdf_id: str, user_id: str = Depends(get_current_user_id)):
    p = await db.pdfs.find_one({"id": pdf_id}, {"status": 1, "pages": 1})
    if not p: raise HTTPException(status_code=404, detail="Non trovato")
    # Check access
    can_access = await _user_can_access_pdf(user_id, pdf_id)
    if not can_access: raise HTTPException(status_code=403, detail="Accesso negato")
    return p

@api.get("/pdfs/{pdf_id}")
async def get_pdf(pdf_id: str, user_id: str = Depends(get_current_user_id)):
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    if not p: raise HTTPException(status_code=404, detail="PDF non trovato")
    # Check access
    can_access = await _user_can_access_pdf(user_id, pdf_id)
    if not can_access: raise HTTPException(status_code=403, detail="Accesso negato")
    return _serialize_pdf(p)

@api.patch("/pdfs/{pdf_id}")
async def patch_pdf(pdf_id: str, payload: PdfPatchIn, user_id: str = Depends(get_current_user_id)):
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    if not p: raise HTTPException(status_code=404, detail="PDF non trovato")
    # Check access
    can_access = await _user_can_access_pdf(user_id, pdf_id)
    if not can_access: raise HTTPException(status_code=403, detail="Accesso negato")
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
    await db.shared_libraries.update_many({}, {"$pull": {"pdf_ids": pdf_id}})
    return {"ok": True}

@api.get("/pdfs/{pdf_id}/file")
async def get_pdf_file(pdf_id: str, token: Optional[str] = Query(None), user_id: Optional[str] = Depends(get_optional_user_id)):
    if not user_id and token: user_id = decode_jwt(token)
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0})
    if not p: raise HTTPException(status_code=404, detail="PDF non trovato")
    if p.get("is_protected") and not user_id: raise HTTPException(status_code=401, detail="Protetto")
    # Check access
    if user_id:
        can_access = await _user_can_access_pdf(user_id, pdf_id)
        if not can_access: raise HTTPException(status_code=403, detail="Accesso negato")
    fpath = Path(p["file_path"])
    if fpath.exists(): return FileResponse(fpath, media_type="application/pdf", filename=p["filename"])
    raise HTTPException(status_code=404, detail="File non trovato")

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
    cursor = db.shared_libraries.find({}, {"_id": 0}).sort("created_at", -1)
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
    await db.shared_libraries.update_one({"id": lib_id}, {"$addToSet": {"pdf_ids": {"$each": payload.pdf_ids}}})
    return {"ok": True}

@api.delete("/libraries/{lib_id}/pdfs/{pdf_id}")
async def remove_from_library(lib_id: str, pdf_id: str, user_id: str = Depends(get_current_user_id)):
    await db.shared_libraries.update_one({"id": lib_id}, {"$pull": {"pdf_ids": pdf_id}})
    return {"ok": True}

@api.delete("/libraries/{lib_id}")
async def delete_library(lib_id: str, user_id: str = Depends(require_admin)):
    await db.shared_libraries.delete_one({"id": lib_id})
    return {"ok": True}

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


async def _user_can_access_pdf(user_id: str, pdf_id: str) -> bool:
    """
    HARD GATE: user must be in PDF's group OR be the owner.
    Group membership is the primary access boundary (group-first model).
    Shared libraries are secondary context (soft filter, not a gate).
    
    Returns True if user has access, False otherwise.
    """
    p = await db.pdfs.find_one({"id": pdf_id}, {"_id": 0, "owner_id": 1, "group_id": 1})
    if not p:
        return False
    
    # Owner can always access their PDF
    if p.get("owner_id") == user_id:
        return True
    
    # HARD GATE: Check if user is in the PDF's group
    group_id = p.get("group_id")
    if group_id:
        grp = await db.groups.find_one(
            {"id": group_id, "members": user_id},
            {"_id": 0, "id": 1}
        )
        if grp:
            # User is in the group → has access
            return True
    
    # User not in group and not owner → no access
    # (shared libraries do not bypass group membership)
    return False

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

@api.get("/admin/logs")
async def admin_logs(event_type: Optional[str] = None, q: Optional[str] = None, limit: int = 100, _: str = Depends(require_admin)):
    query = {}
    if event_type: query["event_type"] = event_type
    if q: query["description"] = {"$regex": re.escape(q), "$options": "i"}
    items = await db.app_logs.find(query).sort("created_at", -1).limit(limit).to_list(limit)
    types = await db.app_logs.distinct("event_type")
    return {"items": [clean_doc(i) for i in items], "types": types}

@api.get("/admin/access-requests")
async def list_access_requests(_: str = Depends(require_admin)):
    reqs = await db.access_requests.find({}).sort("created_at", -1).to_list(100)
    return [clean_doc(r) for r in reqs]

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
            pages_text, total, _ = extract_pages(fpath.read_bytes())
            for i, txt in enumerate(pages_text):
                await db.pdf_pages.update_one({"pdf_id": pdf["id"], "page": i+1}, {"$set": {"text": txt}}, upsert=True)
            await db.pdfs.update_one({"id": pdf["id"]}, {"$set": {"status": "ready", "pages": total}})
            await db.upload_jobs.update_one({"id": job_id}, {"$set": {"status": "completed"}})
    except Exception as e:
        await db.upload_jobs.update_one({"id": job_id}, {"$set": {"status": "failed", "error": str(e)}})
