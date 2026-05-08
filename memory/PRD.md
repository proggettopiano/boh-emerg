# Scorelib — Product Requirements Document

## Original Problem
Web app professionale italiana per pianisti per gestire libreria PDF musicali. Esperienza ricerca tipo Google Search, interfaccia minimal tipo Notion. Solo tema chiaro.

## Architecture
- **Backend**: FastAPI + Motor (MongoDB) + JWT + bcrypt + Resend (email) + Tesseract OCR + PyMuPDF
- **Frontend**: React 19 + react-router-dom 7 + Tailwind + shadcn/ui + react-pdf 9
- **Storage**: PDF locali in `/app/backend/uploads/{user_id}/{pdf_id}.pdf`
- **Index full-text**: collection `pdf_pages` con regex case-insensitive (text index disponibile)

## User Personas
1. **Pianista solista** — carica e cerca i propri spartiti rapidamente
2. **Insegnante / direttore di coro** — crea librerie condivise per allievi/coro
3. **Admin** — monitora i log del sistema

## Core Requirements (static)
- Solo tema chiaro
- Italiano
- Auth email+password e Google (Emergent)
- OCR automatico per scansioni
- Ricerca live tipo Google
- Viewer PDF inline (no download)
- Librerie condivise via link (richiede login)
- Log admin protetto da password (Rome02009)

## Implemented (2026-05-08)
### MVP1 (consegna 1)
- Auth: register, login, JWT, password reset via Resend, Google login (Emergent), rate-limit per IP
- Profile setup (nome, foto, "come ci hai trovato")
- Settings: cambia email/password/nome/foto, toggle backup con warning, cancella account
- Upload multi-PDF: validazione %PDF, compressione PyMuPDF, OCR Tesseract eng+ita, duplicate detector (hash SHA-256 su bytes originali)
- PDF list/get/delete con sort (data, nome) + filtro preferiti + filtro tag
- Search live con snippet, highlight, paginazione, dedup per content_hash, "personale vs condivisa"
- Librerie condivise: create/list/add-pdfs/share-token; `GET /api/shared/{token}` aggiunge come membro; import shared PDF nella propria libreria
- Pagina log admin protetta da `Rome02009` con filtro evento, ricerca, sort

### MVP1.1 (Plan A — viewer + qualità)
- **PDF Viewer reale**: react-pdf 9 + worker locale `/pdf.worker.min.mjs`, scroll continuo, zoom +/-, jump-to-page, highlight ricerca via `customTextRenderer`
- **Preferiti**: stellina sui PDF, filtro "solo preferiti" in libreria
- **Tag manuali**: chips con suggerimenti (jazz/worship/gospel/lead sheet/coro/piano solo/...)
- **Bug fix**: duplicate detector ora hasha i byte originali del PDF (era rotto perché hashava i byte compressi non-deterministici)
- **Admin seed**: `admin@scorelib.app` / `Admin02009!` auto-seeded all'avvio

## Backlog / Next Phases
### P0 (priorità subito)
- **Backup verificabile**: sezione admin con stato backup, data ultimo, count file, pulsante "Esegui ora", "Testa backup", logging dedicato. Decidere destinazione (Emergent object storage / locale zip)
- **Costruttore PDF** ("Crea PDF"): pagina dedicata per assemblare un nuovo PDF da pagine/risultati di altri PDF, salvataggio con nome, download

### P1
- **Modalità offline**: service worker + cache locale dei PDF aperti recenti
- **Annotazioni / disegno** sul viewer: layer canvas sopra react-pdf con strumenti penna/testo, salva come overlay
- **Refactor backend**: split server.py (909 righe) in router separati (auth, pdfs, libraries, admin, settings)

### P2
- Tagging automatico via LLM
- Upload async / job queue per OCR su PDF molto grandi
- Admin role check via JWT + flag `is_admin` per `/api/admin/*`
- Mongo text index attivo (non solo regex)
- Esplicito "join" su libreria condivisa invece di auto-add silenzioso

## Test Credentials
Vedi `/app/memory/test_credentials.md`.

## Known Limitations
- Email validator rifiuta TLD `.local`/`.test`/`.example` → admin email è `admin@scorelib.app`
- OCR sincrono dentro la request di upload (può essere lento su PDF grandi)
- Search regex non scala oltre ~10k pagine
