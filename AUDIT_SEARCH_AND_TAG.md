# AUDIT COMPLETO: SEARCH ALGORITHM E TAG PERSISTENCE

## PROBLEMA 1: SEARCH RANKING BROKEN

### Root Cause: Due problemi combinati

#### 1.1 STRATEGY 3 E 4 SONO TROPPO PERMISSIVE

**Current Implementation** (backend/pdf_processor.py, lines 254-278):

```python
# STRATEGY 3: Scattered word match (50% threshold)
if query_len >= 2:
    matched_count = 0
    for q_token in query_tokens:
        if q_token in doc_tokens:
            matched_count += 1
    
    match_ratio = matched_count / query_len if query_len > 0 else 0
    if match_ratio >= 0.5:  # ← ACCEPTA CON SOLO 50%
        return True

# STRATEGY 4: Permissive window (40% threshold)
if query_len >= 3:
    for start_idx in range(max(0, doc_len - query_len * 2)):
        window = doc_tokens[start_idx : min(start_idx + query_len + 2, doc_len)]
        matching = sum(1 for i, q_token in enumerate(query_tokens) if i < len(window) and window[i] == q_token)
        match_ratio = matching / query_len if query_len > 0 else 0
        
        if match_ratio >= 0.4:  # ← ACCEPTA CON SOLO 40%
            return True
```

**Example Failure**:

```
Query: "quando mi sento solo"
Tokens: ["quando", "mi", "sento", "solo"] (4 parole)

Document A (REALE):
"quando mi sento solo l'incertezza è nel cuor"
Tokens: ["quando", "mi", "sento", "solo", "l", "incertezza", "è", "nel", "cuor"]
Result: Strategy 2 finds EXACT match → CORRECT ✓

Document B (FALSO POSITIVO):
"Io sento qualche cosa quando"
Tokens: ["io", "sento", "qualche", "cosa", "quando"]
- Strategy 3 check: "quando" (✓) + "sento" (✓) = 2/4 = 50%
- Passes threshold >= 0.5
- MATCH RETURNED! ✅ (ma NON correlato)
```

#### 1.2 SCORING NON DIFFERENZIA QUALITY OF MATCH

**Current Scoring** (backend/server.py, lines 1286-1409):

```python
# Hydrated result for cantico number
results.append(format_search_result(p, pg, raw_q, score=100))  # Exact match

# Regex match in title
results.append(format_search_result(p, pg, raw_q, score=30, ...))

# Token-based match in content (BOTH Strategy 1-4)
results.append(format_search_result(p, pg, raw_q, score=10, ...))

# Fallback fuzzy search
results.append(format_search_result(p, pg, raw_q, score=8, ...))
```

**Problem**: Tutti i token matches (Strategies 1-4) ricevono SAME score (10 o 8) indipendentemente da:
- % di parole che matched
- Se è exact phrase match vs scattered words
- Qualità del match

**Consequence**: 
- "quando mi sento solo" (100% exact) = score 10
- "io sento qualche cosa quando" (50% scattered) = score 10
- Dopo sort, ordine è ARBITRARIO (dipende da page number)

#### 1.3 SORTING LOGIC FALLBACK

```python
results.sort(key=lambda x: (-x["score"], x.get("actual_page", x.get("page", 0))))
```

Fallback su page number significa che risultati con STESSO score vengono ordinati per page number, non per relevance!

### Solution Required

**Remove STRATEGY 3 e 4** - too permissive, create false positives
**Add QUALITY-BASED SCORING** - differentiate match quality in score itself

---

## PROBLEMA 2: TAG PERSISTENCE BUG

### Root Cause: Tag filter saved in Library, but NOT propagated

#### 2.1 Current Tag Persistence Flow

**Storage Location**: localStorage (key: "lib_tagFilter")

**Read**: Library.jsx useEffect on mount
```javascript
useEffect(() => {
  const savedTagFilter = localStorage.getItem("lib_tagFilter");
  if (savedTagFilter) {
    setTagFilter(savedTagFilter);
  }
}, []);
```

**Write**: Select onChange
```javascript
onChange={(e) => {
  const newTag = e.target.value;
  setTagFilter(newTag);
  localStorage.setItem("lib_tagFilter", newTag);
}}
```

#### 2.2 Broken Flow: Navigation Away Doesn't Preserve Tag in Home

```
1. User in Library, selects tag "chiesa pomigliano"
   - tagFilter = "chiesa pomigliano"
   - localStorage saved ✓

2. User types search query in Library/Home
   - If in Home.jsx, selectedTag NOT READ FROM ANYWHERE!
   - Home.jsx has NO code to load from localStorage
   - selectedTag in Home.jsx is ALWAYS null unless selected from Library dropdown

3. User clicks result → navigates to PdfViewer
   - Both tagFilter (Library) and selectedTag (Home) are LOST from component state

4. User clicks back/navigates to Library
   - Library remounts → useEffect loads from localStorage ✓
   - But if user was in Home.jsx during search, tagFilter was NEVER there!
```

**Root Cause**: Tag persistence is ONLY in Library.jsx, NOT in Home.jsx search flow.

#### 2.3 Why Tag "Sometimes Disappears"

```
Scenario A (Works):
Library → select tag → see filtered results → back to Library
✓ Works because Library has localStorage persistence

Scenario B (Breaks):
Library → select tag → (implicit navigation to Home search?)
→ search query → click result → back
✗ Tag lost because Home.jsx never reads/saves tag
```

### Solution Required

Tag needs to be:
1. **Global state** (Context or URL parameter)
2. **Persisted in URL** - so back button preserves it
3. **Read by Home.jsx** - when searching with tag

---

## SUMMARY OF ISSUES

| Issue | File | Root Cause | Impact |
|-------|------|-----------|--------|
| Search too permissive | `backend/pdf_processor.py` | Strategy 3&4 with 50%/40% thresholds | False positives, wrong ranking |
| Scoring not differentiated | `backend/server.py` | All token matches get same score | Relevant results not ranked first |
| Tag not in search | `frontend/src/pages/Home.jsx` | No tag loading/persistence | Tag lost when searching from Home |
| Tag lost on navigation | `frontend/src/pages/*` | Tag only in Library, not in URL/Context | Back button resets tag |

## REQUIRED FIXES (in order)

### FIX 1: Remove STRATEGY 3 & 4 (Disable overly permissive matching)
- Keep only STRATEGY 1 & 2 (exact and subsequence)
- This prevents false positives

### FIX 2: Add Quality-Based Scoring
- Score should reflect match quality: exact > subsequence > scattered
- Not just binary match/no-match

### FIX 3: Make tag a URL parameter
- Pass tag via URL state
- Preserves tag on back button

### FIX 4: Load tag in Home.jsx
- Read from URL/localStorage on mount
- Send tag in search request

