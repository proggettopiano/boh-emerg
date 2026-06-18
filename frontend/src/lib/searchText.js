const NOTE_CHORD_RE = /\b(?:DO|RE|MI|FA|SOL|LA|SI)(?:[#b]|[-/][A-Z0-9#b]+|\d+|maj|min|m|dim|aug|sus|add|7|9|11|13)*\b/gi;
const CHORD_RE = /(?<![A-Za-zÀ-ÿ])(?:[A-G](?:#|b)?(?:maj|min|m|dim|aug|sus|add)?\d*(?:\/[A-G](?:#|b)?\d*)?)(?![A-Za-zÀ-ÿ])/gi;
const APOSTROPHE_RE = /[’‘`]/g;
const DECORATIVE_NUMBER_RE = /~\s*\d+\s*~/g;

export function sanitizeSnippetText(value) {
  if (value == null) return "";
  // Preserve original line breaks and sanitize each line independently,
  // then join using a light visual separator (comma + space) so the
  // preview shows where original lines ended without changing the
  // indexed/original text.
  const raw = String(value);
  // If backend preserved newline positions via marker U+23CE (see make_snippet),
  // split on that marker and render with light comma separators for preview only.
  if (raw.indexOf('\u23CE') >= 0) {
    return raw
      .split('\u23CE')
      .map((part) => sanitizeSearchText(part).trim())
      .filter(Boolean)
      .join(', ');
  }
  // If original contains explicit newlines, split and join with comma separator.
  if (/\r?\n/.test(raw)) {
    return raw
      .split(/\r?\n/)
      .map((line) => sanitizeSearchText(line))
      .map((l) => l.trim())
      .filter(Boolean)
      .join(", ");
  }

  // If make_snippet collapsed newlines into '. ' the snippet will contain multiple
  // '. ' separators. Only convert these to commas when the fragments are short
  // (likely line fragments), to avoid touching normal multi-sentence snippets.
  // Only convert when original snippet still contains explicit newlines.
  // This avoids false positives where normal sentences use ". " but are not line breaks.
  const rawHasNewline = /\r?\n/.test(raw);
  if (rawHasNewline) {
    return raw
      .split(/\r?\n/)
      .map((line) => sanitizeSearchText(line))
      .map((l) => l.trim())
      .filter(Boolean)
      .join(", ");
  }

  // Do not touch ". " sequences otherwise — preserve sentence punctuation.
  return sanitizeSearchText(raw);
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
