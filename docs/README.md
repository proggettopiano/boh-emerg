# Documentazione Tecnica

## Diagnosi Tecnica

Il file `DIAGNOSI_TECNICA.md` contiene un'analisi rigorosa e basata sul codice di tre problemi critici identificati nell'applicazione FastAPI + MongoDB deployata su Render:

### Problemi Analizzati

1. **PDF Bloccati in Stato "processing" o "pending" dopo Restart**
   - Gravità: Alta
   - Probabilità: Alta
   - Causa radice: BackgroundTasks non persistenti e mancanza di recovery automatica dei job

2. **Upload PDF su Google Drive senza Login Google**
   - Gravità: Media/Alta
   - Probabilità: Alta
   - Causa radice: Logica di fallback al Master Drive che bypassa l'autenticazione utente

3. **Record PDF Esistenti senza File Corrispondente**
   - Gravità: Media
   - Probabilità: Molto Alta
   - Causa radice: Filesystem effimero di Render e mancanza di storage persistente

### Struttura della Diagnosi

Ogni problema include:
- **Analisi del Codice**: Riferimenti specifici alle righe di codice
- **Evidenze nel Codice**: Frammenti di codice che dimostrano il problema
- **Livello di Gravità e Probabilità**: Valutazione del rischio
- **Fix Minimo Immediato**: Soluzione rapida per mitigare il problema
- **Fix Architetturale Corretto a Lungo Termine**: Soluzione robusta e scalabile

### Come Utilizzare Questa Documentazione

1. Leggi la diagnosi per comprendere i problemi identificati
2. Usa i fix immediati per mitigare i rischi nel breve termine
3. Pianifica l'implementazione dei fix architetturali per una soluzione duratura
4. Condividi questa documentazione con il team per allineare le priorità

---

**Generato da**: Manus AI  
**Data**: 2026-06-04  
**Repository**: proggettopiano/boh-emerg
