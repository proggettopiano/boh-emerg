const NOTE_CHORD_RE = /\b(?:DO|RE|MI|FA|SOL|LA|SI)(?:[#b]|[-/][A-Z0-9#b]+|\d+|maj|min|m|dim|aug|sus|add|7|9|11|13)*\b/gi;
const CHORD_RE = /(?<![A-Za-zÀ-ÿ])(?:[A-G](?:#|b)?(?:maj|min|m|dim|aug|sus|add)?\d*(?:\/[A-G](?:#|b)?\d*)?)(?![A-Za-zÀ-ÿ])/gi;
const APOSTROPHE_RE = /[’‘`]/g;
const DECORATIVE_NUMBER_RE = /~\s*\d+\s*~/g;
const NEWLINE_MARKER_RE = /\u23CE/g;  // ⏎ marker from backend

export function sanitizeSnippetText(value) {
  if (value == null) return "";
  let s = String(value);
  
  // 0. Rimuovi marker di ritorno a capo estratto dal PDF
  s = s.replace(NEWLINE_MARKER_RE, " ");
  
  // 1. normalizza spazi
  s = s.replace(/\s+/g, " ").trim();
  
  // 2. rimuovi accordi musicali (DO, RE, MI, FA#, etc)
  s = s.replace(NOTE_CHORD_RE, " ");
  s = s.replace(CHORD_RE, " ");
  
  // 3. comprime sequenze rumorose di punteggiatura OCR (., ., ., -> , )
  s = s.replace(/(?:[.,]\s*){2,}/g, ", ");
  
  // 4. pulizia base di ", , ,"
  s = s.replace(/(?:,\s*){2,}/g, ", ");
  
  // 5. normalizza spazi multipli di nuovo dopo tutte le pulizie
  s = s.replace(/\s+/g, " ").trim();
  
  return s;
}

export function sanitizeSearchText(value) {
  if (value == null) return "";

  let text = String(value)
    .replace(APOSTROPHE_RE, "'")
    .replace(/\u00a0/g, " ")
    .replace(/\r/g, " ")
    .replace(/\n/g, " ");

  text = text.replace(DECORATIVE_NUMBER_RE, " ");
  text = text.replace(/[œŒ˙…]+/g, " ");
  text = text.replace(NOTE_CHORD_RE, " ");
  text = text.replace(CHORD_RE, " ");
  text = text.replace(/(?<=[A-Za-zÀ-ÿ])\s*[-–—]\s*(?=[A-Za-zÀ-ÿ])/g, "");
  text = text.replace(/[^A-Za-z0-9À-ÿ\s'.]+/g, " ");
  text = text.replace(/(?<=[a-zà-ÿ])(?=[A-ZÀ-Ý][a-zà-ÿ])/g, " ");
  text = text.replace(/\s+/g, " ");
  return text.trim();
}

// Remove chord/note tokens but otherwise preserve the original text content
export function stripChords(value) {
  if (value == null) return "";
  let text = String(value)
    .replace(APOSTROPHE_RE, "'")
    .replace(/\u00a0/g, " ")
    .replace(/\r/g, " ")
    .replace(/\n/g, " ");
  text = text.replace(NOTE_CHORD_RE, " ");
  text = text.replace(CHORD_RE, " ");
  text = text.replace(/\s+/g, " ");
  return text.trim();
}

/**
 * Normalizza query di ricerca per essere "fuzzy-proof":
 * - Capitalizza prima lettera
 * - Normalizza apostrofi
 * - Tollerante a punteggiatura mancante
 * Esempio: "padre se nei pensieri miei" -> "Padre se nei pensieri miei"
 */
export function normalizeSearchQuery(value) {
  if (value == null) return "";
  let q = String(value).trim();
  if (!q) return "";
  
  // Normalizza apostrofi
  q = q.replace(APOSTROPHE_RE, "'");
  
  // Capitalizza prima lettera (per matching case-insensitive nel backend)
  q = q.charAt(0).toUpperCase() + q.slice(1);
  
  // Rimuovi punteggiatura errata a inizio/fine
  q = q.replace(/^[^\w\s]+/, "").replace(/[^\w\s]+$/, "");
  
  // Normalizza spazi multipli
  q = q.replace(/\s+/g, " ");
  
  return q.trim();
}
