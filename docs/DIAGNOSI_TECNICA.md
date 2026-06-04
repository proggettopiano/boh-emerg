# Diagnosi Tecnica App FastAPI + MongoDB su Render

Questa diagnosi tecnica analizza i tre problemi segnalati nell'applicazione FastAPI + MongoDB, fornendo evidenze basate sul codice, cause radice, e suggerimenti per fix immediati e architetturali.

## Contesto dell'Applicazione

L'applicazione gestisce l'upload e l'indicizzazione di PDF, con salvataggio locale e backup opzionale su Google Drive. Utilizza autenticazione JWT e un frontend React. Il backend è implementato con FastAPI e MongoDB, impiegando `BackgroundTasks` per il processamento asincrono dei PDF e librerie come PyMuPDF e pytesseract per l'elaborazione dei documenti.

## Problema 1: PDF Bloccati in Stato "processing" o "pending" dopo Restart

**Descrizione del Problema:** Alcuni PDF rimangono indefinitamente negli stati `processing` o `pending` dopo il riavvio del backend, indicando un fallimento nel completamento o nella ripresa dei job di elaborazione.

### Analisi del Codice

#### 1. Recovery automatica dei job allo startup

Il file `server.py` definisce una funzione `lifespan` (righe 54-72) che viene eseguita all'avvio dell'applicazione. Questa funzione include una logica di migrazione per il campo `status` dei PDF, basandosi sul campo `processing_status` preesistente. In particolare:

- I PDF con `processing_status` non in `["queued", "processing", "uploading", "received"]` e senza `status` vengono impostati a `ready` (righe 59-62).
- I PDF con `processing_status` in `["queued", "processing", "uploading", "received"]` e senza `status` vengono impostati a `pending` (righe 63-66).
- I PDF con `processing_status` in `["failed", "failed_retry"]` e senza `status` vengono impostati a `error` (righe 67-70).

Questa logica si occupa di normalizzare il campo `status` ma **non include una recovery attiva dei job bloccati** nel collection `upload_jobs`. Un PDF in stato `processing` o `queued` prima di un restart verrà impostato a `pending` nella collection `pdfs`, ma il corrispondente `upload_job` rimarrà nello stato in cui si trovava (`processing` o `queued`).

#### 2. Sopravvivenza di `process_pdf_job` ai restart

La funzione `process_pdf_job` (righe 249-379) è una `BackgroundTasks` di FastAPI. Le `BackgroundTasks` sono eseguite in-process e **non sono persistenti ai restart del server**. Se il server si riavvia mentre un `process_pdf_job` è in esecuzione, il task viene terminato e non riprende automaticamente. Il job rimane nello stato `processing` nella collection `upload_jobs` e il PDF associato rimane in `pending` (o `processing_status` a seconda di quale campo viene usato per la visualizzazione).

#### 3. Job bloccati (`upload_jobs`)

La collection `upload_jobs` è centrale per tracciare lo stato di elaborazione. La funzione `process_pdf_job` tenta di "reclamare" un job impostandone lo stato a `processing` solo se è `queued` o `failed_retry` (righe 265-268). Se un job è già in `processing` e il server si riavvia, il job non verrà reclamato da una nuova istanza di `process_pdf_job` a meno che non venga esplicitamente reimpostato a `queued` o `failed_retry`.

Esiste una rotta `/jobs/recover-stuck` (righe 1298-1355) che è progettata per recuperare i PDF bloccati. Questa rotta cerca PDF con `processing_status` in `["queued", "processing"]` che sono stati creati più di 10 minuti fa. Se trova un `existing_job` nella collection `upload_jobs`, lo reimposta a `queued` e aggiorna il `processing_status` del PDF a `queued`. Se non trova un job esistente, ne crea uno nuovo. Questa è la **meccanismo di recovery esistente**, ma richiede di essere invocato esplicitamente (ad esempio, tramite un cron job esterno o un worker dedicato).

#### 4. Record Mongo inconsistenti

Quando un `process_pdf_job` fallisce o viene interrotto (es. per un restart), i record in `pdfs` e `upload_jobs` possono diventare inconsistenti:

- `db.pdfs`: `status` o `processing_status` potrebbe rimanere `processing` o `pending` (se aggiornato dalla `lifespan`).
- `db.upload_jobs`: `status` potrebbe rimanere `processing` e `started_at` conterrebbe un timestamp obsoleto, senza un `finished_at` o un `error` associato.

#### 5. Necessità di un recovery worker allo startup

Attualmente, la `lifespan` non avvia un recovery worker. La rotta `/jobs/recover-stuck` esiste ma deve essere invocata esternamente. Questo significa che senza un meccanismo esterno, i job bloccati rimarranno tali fino a un'invocazione manuale o schedulata di questa rotta.

### Evidenze nel Codice

- **`server.py` (righe 59-70):** Logica di migrazione `status` allo startup, che normalizza lo stato `pending` ma non riavvia i job.
- **`server.py` (righe 265-268):** `process_pdf_job` tenta di reclamare job solo se `queued` o `failed_retry`.
- **`server.py` (righe 1298-1355):** Implementazione della rotta `/jobs/recover-stuck` per la recovery manuale/esterna.
- **`server.py` (riga 1277):** `background_tasks.add_task(process_pdf_job, job_id)` dimostra l'uso di `BackgroundTasks` non persistenti.

### Livello di Gravità e Probabilità

- **Gravità:** Alta. I PDF non vengono elaborati, portando a una cattiva esperienza utente e perdita di funzionalità.
- **Probabilità:** Alta. I restart del server sono eventi comuni in ambienti di produzione (deploy, aggiornamenti, crash, scaling) e la natura non persistente delle `BackgroundTasks` rende questo problema quasi garantito.

### Fix Minimo Immediato

Implementare un meccanismo per invocare la rotta `/jobs/recover-stuck` regolarmente. Questo può essere fatto tramite:

- Un cron job esterno (es. su Render, se supportato, o un servizio esterno come cron-job.org).
- Un worker separato che invoca questa rotta a intervalli regolari.

### Fix Architetturale Corretto a Lungo Termine

Per una soluzione robusta e scalabile, si raccomanda l'adozione di un sistema di coda di messaggi persistente (es. RabbitMQ, Redis Queue, Celery con Redis/RabbitMQ come broker) per la gestione dei job in background. Questo garantirebbe:

- **Persistenza:** I job sopravvivono ai restart del server.
- **Affidabilità:** I job falliti possono essere automaticamente ritentati o spostati in una coda di errori.
- **Scalabilità:** Possibilità di avere più worker che elaborano i job in parallelo.

Inoltre, la `lifespan` dovrebbe includere una logica per ri-accodare i job in stato `processing` o `pending` all'avvio, garantendo che nessun job venga dimenticato.

## Problema 2: Upload PDF su Google Drive senza Login Google

**Descrizione del Problema:** Utenti che non hanno effettuato il login con Google sembrano riuscire a caricare PDF che finiscono nel Google Drive dell'applicazione (o di un altro utente), suggerendo una potenziale falla di autorizzazione.

### Analisi del Codice

#### 1. Selezione del Refresh Token Google

La logica per la risoluzione delle credenziali di backup è definita nella funzione `resolve_backup_credentials` in `server.py` (righe 153-160):

```python
async def resolve_backup_credentials(user: dict) -> Optional[dict]:
    master = await get_master_drive()
    if master and master.get("refresh_token"):
        return {"refresh_token": master["refresh_token"], "owner": "master", "folder_root_id": master.get("folder_root_id"), "email": master.get("email")}
    if user.get("google_refresh_token"):
        return {"refresh_token": user["google_refresh_token"], "owner": "user", "folder_root_id": user.get("drive_folder_id")}
    return None
```

Questa funzione segue una logica di fallback:

1. **Priorità al Master Drive:** Se esiste un `master_drive` configurato nel sistema (tramite `db.system_settings`) e ha un `refresh_token` valido, le credenziali di questo master drive vengono utilizzate. Questo `master_drive` è un account Google configurato a livello di applicazione, non legato a un utente specifico.
2. **Fallback al Drive dell'Utente:** Se non c'è un `master_drive` configurato, o se non ha un `refresh_token`, il sistema cerca il `google_refresh_token` associato all'utente corrente (`user.get("google_refresh_token")`).

La funzione `resolve_upload_credentials` (righe 185-187) chiama `resolve_backup_credentials` passando l'oggetto utente. Questo significa che la logica di fallback è applicata anche per gli upload.

#### 2. Drive Globale vs. Per Utente

- **Drive Globale (Master Drive):** Il codice supporta un "Master Drive" (ottenuto tramite `get_master_drive`, righe 138-143) che è configurato a livello di sistema (`db.system_settings`). Questo Drive è globale per tutta l'applicazione e può essere utilizzato per il backup di tutti i PDF, indipendentemente dall'utente che li ha caricati, se configurato.
- **Drive Per Utente:** Se il Master Drive non è configurato o non ha un token valido, e l'utente ha collegato il proprio account Google, viene utilizzato il Drive personale dell'utente.

#### 3. Falla di Autorizzazione

Il comportamento descritto **non è una falla di autorizzazione, ma un comportamento intenzionale (o una feature) del sistema di backup**. Se un `master_drive` è configurato, tutti gli upload (anche da utenti non loggati con Google) possono essere indirizzati a quel Drive. Questo è particolarmente evidente nella funzione `ensure_drive_upload_folder` (righe 190-205) che, se `creds["owner"] == "master"`, crea una sottocartella per l'utente (`user_id`) all'interno del Master Drive.

Il problema sorge se l'intenzione era che ogni utente dovesse usare *il proprio* Drive, o se il Master Drive non dovesse essere usato per utenti non autenticati con Google. Tuttavia, dal punto di vista del codice, la logica è chiara: il Master Drive ha la precedenza.

### Evidenze nel Codice

- **`server.py` (righe 153-160):** `resolve_backup_credentials` mostra la priorità del `master_drive`.
- **`server.py` (righe 138-143):** `get_master_drive` recupera le credenziali del Master Drive da `db.system_settings`.
- **`server.py` (righe 190-205):** `ensure_drive_upload_folder` crea cartelle utente all'interno del Master Drive se `owner` è `master`.
- **`server.py` (righe 1071-1109):** La logica di upload in `/pdfs/upload-url` e `/pdfs/upload` utilizza `resolve_upload_credentials` per determinare dove salvare il file, e se `creds` è presente, tenta l'upload su Drive.

### Livello di Gravità e Probabilità

- **Gravità:** Media/Alta. Se il Master Drive è configurato e non è inteso per essere un backup globale, può portare a problemi di privacy (dati utente su un Drive condiviso) o di gestione dello spazio. Se è intenzionale, la gravità è bassa.
- **Probabilità:** Alta. Il comportamento è dettato dalla logica implementata e si verificherà ogni volta che un `master_drive` è configurato e un utente carica un PDF.

### Quale account Drive viene utilizzato e in quali condizioni?

- **Account Utilizzato:** Viene utilizzato l'account Google associato al `refresh_token` memorizzato in `db.system_settings` sotto la chiave `master_drive`. Se questo non è presente, viene utilizzato il `google_refresh_token` associato al record dell'utente in `db.users`.
- **Condizioni:** Il Master Drive viene utilizzato se è configurato e ha un `refresh_token` valido. Altrimenti, se l'utente ha collegato il proprio account Google, viene utilizzato il suo Drive personale. Se nessuno dei due è disponibile, il file viene salvato localmente (se la logica di upload lo permette, come nel caso di `/pdfs/upload`).

### Comportamento Intenzionale o Bug?

Dal codice, il comportamento di fallback al Master Drive è **intenzionale**. La priorità data al `master_drive` è esplicita. Se l'intenzione era diversa, allora è un errore di design, non un bug di implementazione.

### Fix Minimo Immediato

Se il comportamento non è desiderato, la soluzione più rapida è **rimuovere la configurazione del `master_drive`** da `db.system_settings`. Questo forzerebbe il sistema a utilizzare il Drive dell'utente (se collegato) o a salvare localmente.

### Fix Architetturale Corretto a Lungo Termine

- **Chiarire la Policy di Backup:** Definire chiaramente se il backup su Drive è un servizio globale dell'applicazione o una funzionalità opt-in per l'utente. Questo dovrebbe essere documentato e comunicato agli utenti.
- **Controllo Granulare:** Se si desidera un Master Drive per scopi specifici (es. backup di sistema) ma non per gli upload utente, la logica in `resolve_backup_credentials` e `resolve_upload_credentials` dovrebbe essere modificata per disabilitare l'uso del Master Drive per gli upload utente, o per richiedere un'esplicita autorizzazione utente anche in presenza di un Master Drive.
- **Interfaccia Utente:** Assicurarsi che l'interfaccia utente rifletta chiaramente dove verranno salvati i file, specialmente se viene utilizzato un Master Drive globale.

## Problema 3: Record PDF Esistenti senza File Corrispondente

**Descrizione del Problema:** Un record PDF esiste nel database (`GET /api/pdfs/{id} -> 200`) ma il file associato non è disponibile (`GET /api/pdfs/{id}/file -> 404`). Questo indica una disconnessione tra il database e lo storage dei file.

### Analisi del Codice

#### 1. `file_path` salvato e `storage_type`

Quando un PDF viene caricato e processato, il campo `file_path` nel record `db.pdfs` viene aggiornato con il percorso locale del file (righe 357 in `process_pdf_job`). Il campo `storage_type` indica se il file è `local` o `google_drive` (riga 356).

#### 2. Logica di recupero file (`get_pdf_file`)

La rotta `GET /api/pdfs/{id}/file` (righe 1559-1589) tenta di recuperare il file:

1. **Storage Locale:** Prima verifica se il file esiste localmente al percorso `UPLOAD_DIR / p["owner_id"] / f"{pdf_id}.pdf"` (riga 1567). Se trovato, lo restituisce.
2. **Fallback a Google Drive:** Se il file locale non esiste (`fpath.exists()` è `False`) e il record PDF ha un `drive_file_id` (riga 1571), tenta di scaricarlo da Google Drive. Se il download ha successo, il file viene salvato localmente (cache) e poi restituito (righe 1581-1585).
3. **Errore 404:** Se il file non è trovato né localmente né su Google Drive (o se il download da Drive fallisce), viene sollevata un'eccezione `HTTPException(status_code=404, detail="File mancante")` (riga 1589).

#### 3. Persistenza del Filesystem su Render

Render, come molti servizi PaaS (Platform as a Service), tipicamente utilizza un filesystem **effimero** per i suoi container. Ciò significa che qualsiasi dato scritto sul filesystem del container (come la directory `uploads`) **non è persistente ai restart, redeploy o scaling orizzontale** del servizio. Ogni volta che un container viene riavviato o un nuovo container viene avviato, il filesystem viene ricreato da zero, e tutti i file precedentemente salvati localmente vengono persi.

### Cause Realistiche di Record Orfani (Ordinate per Probabilità)

1.  **Filesystem Effimero di Render (Probabilità Molto Alta):** Questa è la causa più probabile. I file PDF salvati localmente nella directory `uploads` (come indicato da `UPLOAD_DIR` in `server.py`, riga 38) vengono persi ogni volta che il servizio su Render viene riavviato o ridistribuito. Se un PDF è stato salvato solo localmente (`storage_type = "local"`) e non ha un `drive_file_id` associato, il suo record nel database diventerà orfano dopo un restart del servizio.

2.  **Fallimento del Backup su Google Drive (Probabilità Media):** Se il backup su Google Drive fallisce durante il processo di upload (es. problemi di rete, credenziali Drive non valide, errori API di Google) ma il record del PDF viene comunque creato nel database con `storage_type = "local"` o senza un `drive_file_id` valido, il file sarà accessibile solo localmente fino al prossimo restart. La funzione `upload_pdfs` (righe 1358-1447) salva il file localmente e poi crea il record nel DB. La logica di upload su Drive avviene in `create_pdf_upload_url` (righe 1113-1194) o `complete_pdf_upload` (righe 1248-1280) per gli upload "resumable", o direttamente in `process_pdf_job` (righe 898-916) se il backup è abilitato. Se l'upload su Drive fallisce in questi passaggi, il `drive_file_id` potrebbe non essere impostato correttamente, lasciando il file vulnerabile alla perdita.

3.  **Errore durante il Salvataggio Iniziale (Probabilità Bassa):** Un errore durante la fase di scrittura iniziale del file sul filesystem locale (es. permessi, spazio su disco esaurito, crash del server) potrebbe portare alla creazione del record nel database ma al fallimento del salvataggio del file. Tuttavia, il codice include `try...except` e `tmp_path.unlink` (righe 1437-1446) per gestire questi errori, quindi questo scenario dovrebbe essere meno comune.

4.  **Cancellazione Manuale o Esterna del File (Probabilità Bassa):** Un'azione esterna o manuale che rimuove i file dalla directory `uploads` senza aggiornare il database potrebbe causare record orfani. Questo è meno probabile in un ambiente PaaS come Render, a meno che non ci siano processi esterni non gestiti dall'applicazione.

### Evidenze nel Codice

- **`server.py` (riga 38):** `UPLOAD_DIR = ROOT_DIR / "uploads"` definisce la directory di upload locale.
- **`server.py` (righe 1419-1420):** Il campo `storage_type` e `file_path` vengono impostati nel record PDF.
- **`server.py` (righe 1567-1589):** La rotta `get_pdf_file` mostra la dipendenza dal filesystem locale e il fallback a Google Drive.
- **`server.py` (righe 337-339):** `process_pdf_job` scrive il file sul filesystem locale.

### Fix Minimo Immediato

Abilitare sempre il backup su Google Drive per tutti i PDF. Questo mitigherebbe il problema della persistenza effimera del filesystem di Render, poiché i file sarebbero recuperabili da Drive. Assicurarsi che il `master_drive` sia configurato correttamente o che gli utenti siano incentivati a collegare il proprio account Google.

### Fix Architetturale Corretto a Lungo Termine

1.  **Storage Persistente:** Utilizzare un servizio di storage persistente per i file, come un bucket S3 (o equivalente su altri cloud provider) o un volume persistente su Render (se disponibile e appropriato per il caso d'uso). Questo garantirebbe che i file non vengano persi ai restart del servizio.
2.  **Verifica Consistenza:** Implementare un worker periodico che verifichi la consistenza tra i record del database e i file nello storage (sia locale che Drive). Questo worker potrebbe identificare e segnalare i record orfani, o tentare di recuperare i file mancanti da Drive se un `drive_file_id` è presente.
3.  **Logica di Upload Migliorata:** Rivedere la logica di upload per garantire che il `drive_file_id` venga sempre impostato correttamente e che gli errori di upload su Drive siano gestiti in modo robusto, magari ritentando l'upload o marcando il PDF con uno stato di errore specifico per il backup.

## Conclusione

I problemi riscontrati sono tipici di applicazioni deployate su ambienti PaaS con filesystem effimeri e sistemi di job in background non persistenti. La soluzione a lungo termine richiede un'attenta considerazione dell'architettura di storage e di gestione dei job, mentre i fix immediati possono mitigare gli effetti negativi nell'attesa di implementazioni più robuste.
