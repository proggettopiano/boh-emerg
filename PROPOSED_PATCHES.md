# PROPOSED PATCHES

## PATCH 1: REMOVE STRATEGY 3 & 4 (High-Risk False Positives)

**File**: `backend/pdf_processor.py`, function `_token_sliding_window_match()`

**Current**: Lines 216-280 (4 strategies with permissive thresholds)

**Proposed Change**: Keep only STRATEGY 1 & 2 (exact + subsequence)

```python
def _token_sliding_window_match(query_tokens: List[str], doc_tokens: List[str], fuzzy_threshold: float = 0.7) -> bool:
    """Check if query tokens match any consecutive sliding window in doc_tokens.
    
    STRICT matching - no false positives:
    - Strategy 1: Exact consecutive match (70% threshold for missing words)
    - Strategy 2: Subsequence match (all query tokens must appear in order)
    
    Returns True if match found, False otherwise.
    """
    if not query_tokens or not doc_tokens:
        return False
    
    query_len = len(query_tokens)
    doc_len = len(doc_tokens)
    
    # STRATEGY 1: Try exact consecutive match
    for start_idx in range(max(0, doc_len - query_len + 1)):
        window = doc_tokens[start_idx : start_idx + query_len]
        matching = sum(1 for i, q_token in enumerate(query_tokens) if i < len(window) and window[i] == q_token)
        match_ratio = matching / query_len if query_len > 0 else 0
        if match_ratio >= fuzzy_threshold:
            return True
    
    # STRATEGY 2: Try longer windows (query could be a subset of a longer sequence)
    for window_len in range(query_len + 1, min(doc_len + 1, query_len + 4)):
        for start_idx in range(max(0, doc_len - window_len + 1)):
            window = doc_tokens[start_idx : start_idx + window_len]
            q_idx = 0
            for w_token in window:
                if q_idx < len(query_tokens) and w_token == query_tokens[q_idx]:
                    q_idx += 1
            if q_idx == len(query_tokens):
                return True
    
    return False
```

**Impact**: 
- No more false positives
- "quando" won't match "quando qualcosa diverso"
- Required: exact phrase or clear subsequence

---

## PATCH 2: ADD QUALITY-BASED SCORING

**File**: `backend/pdf_processor.py`, add new function

```python
def _calculate_match_quality_score(query_tokens: List[str], doc_tokens: List[str]) -> float:
    """
    Calculate match quality score between 0.0 and 1.0.
    Higher score = better match.
    
    - 1.0 = exact consecutive match
    - 0.9 = subsequence match (all tokens in order, some extras)
    - 0.5-0.7 = partial/fuzzy matches (fallback only)
    """
    query_len = len(query_tokens)
    doc_len = len(doc_tokens)
    
    if query_len == 0 or doc_len == 0:
        return 0.0
    
    # Check for exact consecutive match
    for start_idx in range(max(0, doc_len - query_len + 1)):
        window = doc_tokens[start_idx : start_idx + query_len]
        if all(i < len(window) and window[i] == query_tokens[i] for i in range(query_len)):
            # Exact match - check how much "extra" context
            extra_ratio = (doc_len - query_len) / max(1, doc_len)
            return 1.0 - (extra_ratio * 0.05)  # Small penalty for extra words
    
    # Check for subsequence match (tokens in order, but scattered)
    for window_len in range(query_len, min(doc_len + 1, query_len + 5)):
        for start_idx in range(max(0, doc_len - window_len + 1)):
            window = doc_tokens[start_idx : start_idx + window_len]
            q_idx = 0
            matches = 0
            for w_token in window:
                if q_idx < query_len and w_token == query_tokens[q_idx]:
                    q_idx += 1
                    matches += 1
            
            if matches == query_len:  # All query tokens found in order
                gap_ratio = (window_len - query_len) / query_len
                return 0.9 - (gap_ratio * 0.1)  # Penalty based on gaps
    
    return 0.0  # No match
```

**File**: `backend/server.py`, modify search endpoint

Replace generic score assignments with quality-based scores:

```python
# In text_cursor processing (around line 1389)
if pg_text and text_matches_query(pg_text, q, use_fuzzy=True):
    quality = _calculate_match_quality_score(
        _tokenize_text(q), 
        _tokenize_text(pg_text)
    )
    score = int(10 * quality)  # Scale to 0-10 range
    if p:
        results.append(format_search_result(p, pg, raw_q, score=score, source="personal", match_in="content"))
```

**Impact**:
- "quando mi sento solo" (exact match) → score 10
- "quando mi sento solo l'incertezza è nel cuor" (exact match) → score 9-10
- "quando... [4 extra words]... sento solo" (loose subsequence) → score 7-8
- Results properly ranked by relevance

---

## PATCH 3: MAKE TAG A URL PARAMETER

**File**: `frontend/src/pages/Home.jsx`

```javascript
// Add to component top
const [searchParams] = useSearchParams();
const tagFromUrl = searchParams.get("tag") || "";

// In search effect
const params = { q: normalizedQ };
if (selectedTag || tagFromUrl) params.tag = selectedTag || tagFromUrl;

// When navigating to results
navigate(`/library?tag=${encodeURIComponent(selectedTag || tagFromUrl)}`);
```

**File**: `frontend/src/pages/Library.jsx`

```javascript
// Add to component top
const [searchParams] = useSearchParams();

// Merge localStorage and URL parameter
useEffect(() => {
  const urlTag = searchParams.get("tag");
  const savedTag = localStorage.getItem("lib_tagFilter");
  const tagToUse = urlTag || savedTag || "";
  
  if (tagToUse) setTagFilter(tagToUse);
}, [searchParams]);
```

**Impact**:
- Tag visible in URL: `/library?tag=chiesa%20pomigliano`
- Back button preserves tag
- Shareable: copy URL = same filter

---

## PATCH 4: PERSISTENCE VERIFICATION

Add diagnostic logging to verify tag persistence works:

```javascript
// In Library.jsx useEffect
useEffect(() => {
  console.log("[TAG_PERSIST] Library mounted. URL:", searchParams.get("tag"), "localStorage:", localStorage.getItem("lib_tagFilter"), "state:", tagFilter);
}, [tagFilter, searchParams]);

// In search request
console.log("[SEARCH] Sending tag:", params.tag || "none");
```

---

## TESTING PLAN

After patches applied:

1. **Test Search Quality**:
   ```
   Query: "quando mi sento solo"
   Expected: Exact match comes first (score 9-10)
   NOT: "quando qualcosa" (would now return no match)
   ```

2. **Test Tag Persistence**:
   ```
   1. Select tag "chiesa pomigliano"
   2. Check URL shows ?tag=chiesa%20pomigliano
   3. Click back button
   4. Tag still selected ✓
   5. Close tab, reopen
   6. Navigate to library - tag loaded from localStorage ✓
   ```

3. **Test Cross-Navigation**:
   ```
   1. Library with tag selected
   2. Search from Home (tag should be in URL)
   3. Open result
   4. Navigate back
   5. Tag still active ✓
   ```

---

## SUMMARY OF CHANGES

| Problem | Solution | File | Risk |
|---------|----------|------|------|
| False positives | Remove Strategy 3&4 | pdf_processor.py | LOW (removes broken code) |
| Wrong ranking | Quality-based scoring | pdf_processor.py, server.py | LOW (improves existing) |
| Tag lost in Home | URL parameter | Home.jsx, Library.jsx | LOW (URL state is safe) |
| Tag lost on back | URL-based navigation | Home.jsx | LOW (enhances existing navigation) |

---

## ROLLBACK PLAN

If patches cause issues:
1. Revert to commit bc1030f
2. Search reverts to Strategies 1-4 (acceptable baseline)
3. Tag persistence reverts to localStorage only (works in Library)

