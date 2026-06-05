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

class CreateLibraryIn(BaseModel):
    name: str
    description: Optional[str] = None

class AddPdfsIn(BaseModel):
    pdf_ids: List[str]

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

@api.get("/pdfs")
async def list_pdfs(user_id: str = Depends(get_current_user_id)):
    cursor = db.pdfs.find({}, {"_id": 0}).sort("created_at", -1)
    items = await cursor.to_list(1000)
    return {"items": [_serialize_pdf(i) for i in items]}

@api.post("/pdfs/upload")
async def upload_pdf(background_tasks: BackgroundTasks, files: List[UploadFile] = File(...), user_id: str = Depends(get_current_user_id)):
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
            "owner_id": user_id,
            "compressed": was_compressed,
            "created_at": iso_now(),
        })

        job_id = str(uuid.uuid4())
        await db.upload_jobs.insert_one({"id": job_id, "pdf_id": pdf_id, "status": "queued", "created_at": iso_now()})
        background_tasks.add_task(process_pdf_job, job_id)

        results.append({"ok": True, "pdf_id": pdf_id, "name": filename, "compressed": was_compressed})

    return {"results": results}

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


@api.get("/shared/{token}")
async def view_shared(token: str, request: Request, user_id: Optional[str] = Depends(get_optional_user_id)):
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
            pages_text, total, _, _ = extract_pages(fpath.read_bytes())
            for i, txt in enumerate(pages_text):
                await db.pdf_pages.update_one({"pdf_id": pdf["id"], "page": i+1}, {"$set": {"text": txt}}, upsert=True)
            await db.pdfs.update_one({"id": pdf["id"]}, {"$set": {"status": "ready", "pages": total}})
            await db.upload_jobs.update_one({"id": job_id}, {"$set": {"status": "completed"}})
    except Exception as e:
        await db.upload_jobs.update_one({"id": job_id}, {"$set": {"status": "failed", "error": str(e)}})
