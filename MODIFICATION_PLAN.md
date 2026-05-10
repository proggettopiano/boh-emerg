# 🔍 PIANO ANALISI SCORELIB - MODIFICHE SICURE

**Data**: 2026-05-10  
**Status**: PRE-MODIFICA - Approvazione utente richiesta  
**Progetto**: ScoreLib (Production)

---

## 📊 RIEPILOGO PROBLEMI TROVATI

### 1️⃣ EMPTY CATCH BLOCKS (CRITICAL - Perdita errori)

#### A. Frontend - PdfViewer.jsx (ALTO RISCHIO)
- **Linea 69**: `catch { return str; }` in `customTextRenderer`
  - Context: Try/catch su RegExp per highlighting testo
  - Rischio: Silent failure su regex malformata - testo non highlighted ma nessun errore visibile
  - Fix: Loggare errore + return str senza highlight
  - Impact su: Ricerca PDF con caratteri speciali

- **Linea ~151**: `catch { toast.error("Errore"); }` in `toggleFavorite`
  - Context: Patch preferito
  - Rischio: Errore generico senza dettagli - UX confusa
  - Fix: Log specifico + toast con dettagli
  - Impact su: Azione preferiti

#### B. Frontend - Home.jsx (BASSO RISCHIO)
- **Linea 14**: `catch { return text; }` in `highlight()`
  - Context: Highlight query in risultati ricerca
  - Rischio: Same as PdfViewer - regex silenzioso
  - Fix: Loggare + return testo non highlighted
  - Impact su: Ricerca con query speciali

#### C. Backend - server.py (Molte exception catturate bene)
- ✅ **Buone pratiche**: Log event per errori, HTTPException con dettagli
- Nessun empty catch block trovato nel backend

### 2️⃣ HARDCODED SECRETS (CRITICAL - Security)

#### A. conftest.py (ALTO RISCHIO)
```python
ADMIN_PASSWORD = "Admin02009!"  # ← HARDCODED
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://sheet-music-hub-4.preview.emergentagent.com")
```
- Rischio: Password visibile in Git, test suite con credenziali fisse
- Fix: `.env.test` file (gitignored)
- Colpisce: test_backend.py, test_admin_iter3.py, etc.

#### B. test_admin_iter3.py (MEDIO RISCHIO)
```python
json={"password": "Rome02009"}  # ← HARDCODED
```
- Rischio: Admin log password visibile in test
- Fix: Spostare in `.env.test`

#### C. server.py (MEDIO RISCHIO)
```python
ADMIN_LOG_PASSWORD = os.environ.get("ADMIN_LOG_PASSWORD", "Rome02009")  # ← DEFAULT INSICURO
```
- Rischio: Fallback a password pubblica se env mancante
- Fix: Esigere env obbligatorio in produzione
- Note: Non è un secret hardcoded ma è un default pericoloso

#### D. test_google_backup.py (BASSO RISCHIO - Probably public)
```python
EXPECTED_CLIENT_ID = "239524592693-qhl4tacfd7t1ids24bq9tq5dj31a8mlk.apps.googleusercontent.com"
```
- Rischio: OAuth Client ID è pubblico (definizione) ma espone credenziali
- Fix: Spostare in fixture o `.env.test`

### 3️⃣ PYTHON `is` vs `==` (BASSO RISCHIO - Non trovati)

- ✅ **Risultato**: Nessun confronto errato trovato nel codice Python
- backend/server.py: usa confronti corretti (`if not u.get("password_hash") or not verify_password`)
- backend/pdf_processor.py: usa confronti corretti (`if len(text) < MIN_CHARS_PER_PAGE`)

### 4️⃣ REACT HOOK DEPENDENCIES (MEDIO RISCHIO - Potenziale bug)

#### A. PdfViewer.jsx (MASSIMO RISCHIO - File fragile!)

**Linea 82-90**: useEffect con collectMatches
```javascript
useEffect(() => {
  if (numPages > 0 && renderedPages >= numPages) {
    collectMatches();
  }
  // eslint-disable-next-line
}, [renderedPages, numPages, queryStr]);
```
- ⚠️ Comment says "eslint-disable-line" ma include collectMatches nel corpo
- Rischio: collectMatches non in dependencies - potrebbe stale
- MA: collectMatches usa useCallback([queryStr]) - ok ma fragile
- Fix: Aggiungere collectMatches alle dependencies
- **DANGER**: PdfViewer è fragile - piccoli cambi causano crash

**Linea 100-111**: useEffect per jump pagina iniziale
```javascript
useEffect(() => {
  if (numPages > 0 && initialPage > 1) {
    setTimeout(() => {
      const el = pageRefs.current[initialPage];
      if (el) el.scrollIntoView({ behavior: "auto", block: "start" });
    }, 200);
  }
  // eslint-disable-next-line
}, [numPages]);
```
- ⚠️ Missing: initialPage in dependencies (ma è constante da params)
- Marginal risk: initialPage non cambia dopo mount

**Linea 122-132**: Keyboard nav useEffect
```javascript
useEffect(() => {
  const onKey = (e) => { ... };
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}, [matches, matchIndex, queryStr]); // eslint-disable-line
```
- ✅ Dependencies sembra ok

#### B. AuthContext.jsx (MEDIO RISCHIO)

**Linea 17-23**: fetchMe useCallback
```javascript
const fetchMe = useCallback(async () => {
  const token = localStorage.getItem("scorelib_token");
  if (!token) { setUser(null); setLoading(false); return; }
  try {
    const r = await api.get("/auth/me");
    setUser(r.data);
  } catch {
    localStorage.removeItem("scorelib_token");
    setUser(null);
  } finally {
    setLoading(false);
  }
}, []);
```
- Dipendenze: `[]` - corretto, fetchMe non dipende da stato
- ✅ Ok

**Linea 26-33**: useEffect per chiamare fetchMe
```javascript
useEffect(() => {
  if (window.location.hash?.includes("session_id=")) {
    setLoading(false);
    return;
  }
  fetchMe();
}, [fetchMe]);
```
- ✅ fetchMe in dependencies - corretto
- Ma: fetchMe è stabile (useCallback con []) - non causa cicli
- ✅ Ok

#### C. Library.jsx (BASSO RISCHIO)

**Linea ~31**: useEffect load
```javascript
useEffect(() => { load(); }, [sort, favOnly, tagFilter]); // eslint-disable-line
```
- ⚠️ Comment says eslint-disable ma dipendenze specificate
- Rischio: load() non è memoizzato - rischio render loop se load non stabile
- Ma: load() è definito inline - ricalcolato ogni render
- Fix: useMemo su load() o useCallback
- **Risk Level**: Basso - search non attivo durante load

---

## 🔥 MODIFICHE PROPOSTE (CON RISCHI)

### TIER 1: MODIFICHE SICURE (Rischio: MINIMO)
| File | Tipo | Azione | Rischio | Test |
|------|------|--------|--------|------|
| `conftest.py` | Secrets | Spostare `ADMIN_PASSWORD` → `.env.test` | NONE | ✅ |
| `conftest.py` | Secrets | Spostare `BASE_URL` → `.env.test` | NONE | ✅ |
| `server.py` | Secrets | Add ADMIN_LOG_PASSWORD required in prod | LOW | ✅ |
| `test_admin_iter3.py` | Secrets | Sostituire hardcoded password → `os.getenv("ADMIN_LOG_PASSWORD")` | NONE | ✅ |
| `test_google_backup.py` | Secrets | Spostare CLIENT_ID → `.env.test` | NONE | ✅ |

### TIER 2: MODIFICHE A RISCHIO BASSO (Rischio: BASSO)
| File | Tipo | Azione | Rischio | Test | Note |
|------|------|--------|--------|------|------|
| `Home.jsx` | Logging | Aggiungere console.error in catch (highlight) | NONE | Manual | Non tocca logica |
| `server.py` | Logging | Logging è già buono | SKIP | - | Nessun problema trovato |

### TIER 3: MODIFICHE A RISCHIO MEDIO (Rischio: MEDIO - RICHIEDE CAUTELA)
| File | Tipo | Azione | Rischio | Test | Note |
|------|------|--------|--------|------|------|
| `PdfViewer.jsx` L69 | Logging | Aggiungere console.error in catch | NONE | E2E | Non tocca logica regex |
| `PdfViewer.jsx` L151 | Logging | Aggiungere console.error in catch | LOW | E2E | Non tocca preferiti |
| `PdfViewer.jsx` L82 | Hooks | Verifica collectMatches dependency | **CAUTION** | E2E | **MASSIMO RISCHIO SE TOCCATO** - Già fragile |
| `Library.jsx` L31 | Hooks | Memoizzare load() | **CAUTION** | E2E | **Potenziale loop** |

### TIER 4: NON MODIFICARE
| File | Motivo | Reason |
|------|--------|--------|
| `PdfViewer.jsx` | Refactor completo | User richiede: NON toccare refactor - troppo fragile |
| `AuthContext.jsx` | Hook dependencies | Già corretto - nessun problema |
| `pdf_processor.py` | Python is/== | Nessun problema trovato |

---

## ⚠️ RISCHI SPECIFICI PER FILE FRAGILE

### PdfViewer.jsx - MASSIMA CAUTELA

```
┌─ PdfViewer.jsx (FRAGILE)
├─ useEffect dependencies con eslint-disable
├─ Cerca PDF (regex highlight)
├─ Sticky search bar
├─ Apertura documento PDF (render pages)
└─ ⚠️ RECENTE: PDF crash fix (sendWithPromise null) ← NON ROMPERE
```

**Cosa NON fare**:
- ❌ Split in sub-components
- ❌ Refactor hooks
- ❌ Cambiar dependencies senza test E2E
- ❌ Modificare renderTextLayer
- ❌ Toccare pageRefs logic

**Cosa SI può fare SAFE**:
- ✅ Aggiungere console.error in catch blocks (non tocca logica)
- ✅ Aggiungere logging per ricerca

---

## 📋 CHECKLIST PRE-MODIFICA

- [ ] Utente approva piano
- [ ] Backup database (MongoDB)
- [ ] Build frontend locale
- [ ] Run test suite complete
- [ ] Verificare nessun warning ESLint nuovo

---

## ✅ MODIFICHE APPROVATE vs NEGATE

Attendo approvazione per:

1. **TIER 1 (SAFE)**: Spostare secrets in `.env.test` + environment require
2. **Logging semplice**: console.error in catch blocks
3. **Rimanere cauti**: PdfViewer, Library dependencies

**NON fare** senza approvazione specifica:
- Refactor PdfViewer
- Migrare a httpOnly cookies
- TypeScript migration
- Cambiar login flow
- Split componenti
