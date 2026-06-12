const NOTE_CHORD_RE = /\b(?:DO|RE|MI|FA|SOL|LA|SI)(?:[#b]|[-/][A-Z0-9#b]+|\d+|maj|min|m|dim|aug|sus|add|7|9|11|13)*\b/g;
const CHORD_RE = /(?<![A-Za-zÀ-ÿ])(?:[A-G](?:#|b)?(?:maj|min|m|dim|aug|sus|add)?\d*(?:\/[A-G](?:#|b)?\d*)?)(?![A-Za-zÀ-ÿ])/gi;

export function sanitizeSnippetText(value) {
  return sanitizeSearchText(value);
}

export function sanitizeSearchText(value) {
  if (value == null) return "";

  let text = String(value)
    .replace(/\u00a0/g, " ")
    .replace(/\r/g, " ")
    .replace(/\n/g, " ");

  text = text.replace(/[œŒ˙…]+/g, " ");
  text = text.replace(NOTE_CHORD_RE, " ");
  text = text.replace(CHORD_RE, " ");
  text = text.replace(/(?<=[A-Za-zÀ-ÿ])\s*[-–—]\s*(?=[A-Za-zÀ-ÿ])/g, "");
  text = text.replace(/[^A-Za-z0-9À-ÿ\s]/g, " ");
  text = text.replace(/(?<=[a-zà-ÿ])(?=[A-ZÀ-Ý][a-zà-ÿ])/g, " ");
  text = text.replace(/\s+/g, " ");
  return text.trim();
}

// Remove chord/note tokens but otherwise preserve the original text content
export function stripChords(value) {
  if (value == null) return "";
  let text = String(value).replace(/\u00a0/g, " ").replace(/\r/g, " ").replace(/\n/g, " ");
  text = text.replace(NOTE_CHORD_RE, " ");
  text = text.replace(CHORD_RE, " ");
  text = text.replace(/\s+/g, " ");
  return text.trim();
}
