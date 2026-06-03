# ScoreLib

ScoreLib è una libreria personale per spartiti e PDF musicali. Il progetto combina un frontend React con un backend FastAPI per caricare PDF, indicizzarli con estrazione testo/OCR, cercare rapidamente tra le pagine, gestire preferiti e tag, condividere librerie e usare Google Drive come storage/backup opzionale.

## Stack

| Area | Tecnologia |
|---|---|
| Frontend | React, CRACO, Tailwind CSS, Radix UI, react-pdf |
| Backend | FastAPI, Motor/MongoDB, PyMuPDF, Tesseract OCR |
| Storage | Locale di default, Google Drive opzionale |
| Email | Resend opzionale per reset password e verifica email |
| Mobile | PWA-ready e configurazione Capacitor per Android |

## Avvio locale

### Backend

Copia il file di esempio e configura almeno database e segreto JWT.

```bash
cd backend
cp .env.example .env
pip install -r requirements.txt
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

Il backend espone le API sotto `/api`. Per l'upload locale è sufficiente MongoDB; Google Drive e Resend sono opzionali.

### Frontend

```bash
cd frontend
npm install
REACT_APP_API_URL=http://localhost:8000 npm start
```

In produzione imposta `REACT_APP_API_URL` sulla root del backend, per esempio `https://scorelib-backend.onrender.com`. Non hardcodare URL backend nei sorgenti React.

## Variabili ambiente principali

| Variabile | Obbligatoria | Descrizione |
|---|---:|---|
| `MONGO_URL` | Sì | Connessione MongoDB. |
| `DB_NAME` | Sì | Nome database. |
| `JWT_SECRET` | Sì | Segreto per firmare i token di sessione. |
| `BACKEND_CORS_ORIGINS` | Consigliata | Origini frontend abilitate, separate da virgola. |
| `MAX_UPLOAD_SIZE_BYTES` | No | Limite dimensione upload, default 50 MB. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | No | Abilitano login/backup Google Drive. |
| `RESEND_API_KEY` | No | Abilita email reali; senza chiave i link vengono loggati. |
| `WORKER_SECRET` | No | Protegge l'endpoint manuale di processing job. |

## Comandi utili

| Comando | Scopo |
|---|---|
| `npm run build` in `frontend/` | Build frontend di produzione. |
| `python -m py_compile backend/*.py` | Controllo sintassi backend. |
| `npm run android:init` | Inizializza progetto Android Capacitor. |
| `npm run android:apk` | Build APK debug dopo setup Android SDK/JDK. |

## Note di deploy

Per Vercel configura il frontend con `REACT_APP_API_URL`. Per Render/Fly/Railway o servizi simili configura il backend con le variabili presenti in `backend/.env.example`, abilita MongoDB e imposta `BACKEND_CORS_ORIGINS` includendo il dominio frontend.

## Miglioramenti recenti

Questo repository include una pipeline di upload asincrona: il file viene ricevuto, messo in coda, compresso se utile, indicizzato con OCR quando necessario e poi reso disponibile in libreria. La UI mostra ora in modo più accurato lo stato di storage, OCR e compressione durante il polling dello stato PDF.
