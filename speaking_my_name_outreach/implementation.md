# #SpeakingMyName Outreach - Line-by-Line Code Walkthrough

Full architecture: `../IMPLEMENTATION.md`. Shared modules: `../shared/implementation.md`.

## File map

```
speaking_my_name_outreach/
├── generate_smn_emails.py   ← two-phase: parallel research, sequential generation
├── smn_prompt.py            ← AI instructions + CAMPAIGN dict
├── smn_send.js              ← Gmail sender (Apps Script) with Hana's video attachment
└── _data/                   ← #SpeakingMyName Outreach Tracker.xlsx
```

---

## `generate_smn_emails.py` - Line by Line

### Imports (lines 20-35)

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
```

**Why ThreadPoolExecutor:** The SMN script is the only campaign that runs research in parallel. 8 workers simultaneously search for org websites, scrape pages, and extract text. This cuts research time from ~2 minutes per org to ~15 seconds per batch.

```python
from shared.generator import (
    fix_windows_encoding, parse_args, call_llm,
    clean_email, safe_str, connect_sheet, get_sheet_id,
)
from smn_prompt import get_prompt, CAMPAIGN
```

Same shared import pattern as all campaign scripts. `smn_prompt.py` provides the SMN-specific AI instructions.

### XLSX Reader (lines 48-88) - `read_orgs_from_xlsx()`

```python
for row_idx, row in enumerate(ws.iter_rows(min_row=3, values_only=True), 3):
    vals = [safe_str(v) for v in row]
```

Starts at row 3 because rows 1-2 are header/metadata rows in the SMN tracker. Every cell value is immediately wrapped in `safe_str()` to normalize NaN/None.

```python
full_name        = vals[0] if len(vals) > 0 else ''
association_name = vals[2] if len(vals) > 2 else ''
```

Fixed column positions. The SMN tracker has a consistent schema: A=full_name, B=title, C=association_name, D=org_type, E=email, F=reached_out, G=date, H=response, I=notes.

```python
if association_name.lower() in ('n/a', 'none', 'test', ''):
    continue
```

Filters out placeholder rows. Some entries in the tracker are test data or have no real organization.

```python
is_followup = (reached_out.lower() == 'yes' and response.lower() == 'pending')
```

**The follow-up detection rule:** If someone from SpeakHire already reached out AND hasn't heard back, the AI writes a follow-up email that references the prior contact instead of a cold introduction. This is a single boolean computed once and carried through the entire pipeline.

### Website Research (lines 95-302) - Three-tier fallback

```python
_MISSION_PATHS = [
    "/about", "/about-us", "/who-we-are", "/our-story", "/mission",
    "/what-we-do", "/our-work", "/programs", "/services", "/initiatives",
    "/impact", "/community", "/diversity", "/dei", "/inclusion",
    "/values", "/our-values", "/who-we-serve",
]
```

**16 common sub-paths.** After scraping the homepage, the script tries each of these paths on the org's domain. Only the first 10 are actually used (`_MISSION_PATHS[:10]`) to keep things fast. Paths like `/diversity`, `/dei`, and `/inclusion` are specifically included because they're likely to contain the DEI-related content the AI needs for personalization.

#### `search_org_website()` - Three strategies

**Strategy 0 - Email domain (lines 134-147):**
```python
if email_hint and '@' in email_hint:
    email_domain = email_hint.split('@')[-1].strip().lower()
    skip_email_domains = ['gmail.com', 'yahoo.com', 'hotmail.com', ...]
```
Fastest and most accurate. If the contact's email is `lisa@acmecorp.org`, the script tries `acmecorp.org` directly. Timeout is just 1-2 seconds per attempt.

**Strategy 1 - Pattern guessing (lines 149-169):**
```python
for suffix in [' inc', ' inc.', ' ltd', ' llc', ', inc', ', inc.', ' the ']:
    clean = clean.replace(suffix, '')
```
Strips legal suffixes before guessing domain names. "Acme Corp Inc." becomes "acmecorp" → tries `acmecorp.org`, `acmecorpnyc.org`. The `nyc.org` variant is NYC-specific since most SMN partner orgs are local.

```python
session = requests.Session()
```
**Session reuse:** A `requests.Session()` keeps the TCP connection alive across the 3 pattern attempts. Faster than creating a new connection for each URL.

**Strategy 2 - Bing search (lines 172-193):**
```python
resp = requests.get(
    "https://www.bing.com/search",
    params={"q": f'"{org_name}" site:.org OR site:.com'},
)
```
Last resort. Searches Bing for the exact org name, restricted to .org and .com domains. Results are filtered to exclude social media, Wikipedia, and job sites.

```python
for a_tag in soup.select("h2 a")[:5]:
    href = a_tag.get("href", "")
    if href and "://" in href and "bing.com" not in href:
        domain = urllib.parse.urlparse(href).netloc.lower()
        if not any(s in domain for s in ["facebook.com", "linkedin.com", ...]):
            return href
```

**Domain filtering:** Bing results often include Facebook, LinkedIn, and Glassdoor pages for the org. The script skips these and returns only the org's actual website.

#### `_extract_key_info()` - Two-tier keyword matching

```python
keywords = [
    "mission", "vision", "serve", "community", "program", "diversity",
    "equity", "inclusion", "belonging", "immigrant", "refugee", "youth",
    ...38 total keywords...
]
```

38 keywords covering mission, community, identity, and accessibility themes. These were chosen specifically because they match the types of programs the SMN AI prompt needs to reference.

```python
if org_name.lower().split()[0] in s_lower and any(kw in s_lower for kw in keywords):
    matches.append(s[:300])
elif any(kw in s_lower for kw in keywords[:8]) and any(kw in s_lower for kw in keywords[8:]):
    matches.append(s[:300])
```

**Two-tier matching:**
1. **Tier 1:** Sentence contains the org's first name word AND any keyword. High-precision match - this sentence is almost certainly about the org.
2. **Tier 2:** Sentence contains keywords from BOTH the first half AND second half of the keyword list. Broader match for pages that don't mention the org by name.

```python
seen, unique = set(), []
for m in matches:
    prefix = m[:40].lower()
    if prefix not in seen:
        seen.add(prefix)
        unique.append(m)
return unique[:8]
```

**Deduplication by first 40 characters.** Returns up to 8 unique sentences. This is more generous than the Soiree script (6 sentences) because SMN needs richer context for the deeper personalization rules.

#### `research_org()` - Deep multi-page scraping

```python
all_texts = [f"HOMEPAGE ({homepage.get('title', 'No title')}):\n{homepage['text']}"]
visited_urls = {website_url.rstrip("/"), homepage["url"].rstrip("/")}
```

Tracks visited URLs to avoid re-scraping. The homepage URL and its redirect target are both marked as visited (hence the set with two entries).

```python
for path in _MISSION_PATHS[:10]:
    sub_url = f"{base}{path}"
    if sub_url.rstrip("/") in visited_urls:
        continue
    visited_urls.add(sub_url.rstrip("/"))
    page = fetch_url_text(sub_url, timeout=8)
    if not page["error"] and len(page["text"]) > 200:
        all_texts.append(f"[{label}]: {page['text'][:2500]}")
```

**Sub-page scraping with guards:** Only fetches if not already visited. Only keeps pages with >200 chars of actual text (filters out empty or JS-only pages). Each sub-page is limited to 2500 chars (vs 5000 for the homepage) to keep total context under LLM limits.

```python
# Search for DEI/inclusion programs
dei_q = f'"{org_name}" diversity equity inclusion belonging program'
resp = requests.get("https://html.duckduckgo.com/html/", params={"q": dei_q}, ...)
```

**DuckDuckGo DEI search:** Uses the HTML (non-JS) version of DuckDuckGo to avoid rate limiting. Searches for the org name plus DEI keywords. The results are parsed for snippet text, which gives the AI specific DEI program names to reference.

```python
research_notes = "\n".join(research_notes_parts)       # For the sheet (column L)
full_research_text = "\n\n".join(context_parts)[:4000]  # For the LLM prompt
```

**Two output formats:** `research_notes` is human-readable and goes to column L of the sheet. `full_research_text` is richer (includes DEI search results) and goes into the LLM prompt, capped at 4000 chars.

### Email Generation (lines 309-361) - `generate_email_for_org()`

```python
followup_label = "FOLLOW-UP: YES" if org['is_followup'] else "FOLLOW-UP: NO"
```

The AI prompt gets an explicit `FOLLOW-UP: YES/NO` flag. The system prompt in `smn_prompt.py` has separate instructions for each case - follow-ups acknowledge prior contact and build on it; fresh emails give a full campaign introduction.

```python
if org['is_followup']:
    prior_context = f"""
PRIOR CONTACT CONTEXT:
Our team (Hetal/Hana) reached out to {org['full_name'] or 'the team'} at {org['association_name']} in March 2026.
Current response status: {org['response']}
The email should briefly acknowledge this prior contact and build on it."""
```

**Rich follow-up context:** The AI knows who reached out, when, and what the response status is. This lets it write "I wanted to circle back as the June 16th campaign date is now just days away" instead of a generic follow-up.

```python
result = call_llm(system_prompt, user_prompt)
subject = clean_email(result.get('email_subject', ''))
body = clean_email(result.get('email_body', ''))
```

Note the key is `email_body` (not `email_draft`). The SMN prompt returns a simpler JSON with just `email_subject` and `email_body`, unlike the Soiree/Outreach prompts which return 11-field evidence-tracking JSON.

### Sheet Sync (lines 368-423)

```python
def get_existing_rows(ws):
    existing = {}
    for ri, row in enumerate(all_vals[1:], start=2):
        assoc = safe_str(row[2]) if len(row) > 2 else ''
        if assoc:
            existing[assoc.lower().strip()] = {
                'sheet_row': ri,
                'status': safe_str(row[5]),
                ...
            }
    return existing
```

**Org name as dedup key.** The association name (lowercased, stripped) is the unique identifier. If an org appears in both the xlsx and the sheet, it's matched by name and not re-imported.

```python
def import_orgs_to_sheet(orgs, ws):
    for org in orgs:
        key = org['association_name'].lower().strip()
        if key in existing:
            continue                  # Already in sheet - skip
        sheet_row = len(existing) + imported + 2
```

**Incremental row numbering:** New orgs are appended below existing ones. `len(existing) + imported + 2` calculates the next available row (existing count + new imports so far + header row offset).

### Main - Two-Phase Architecture (lines 430-594)

#### Phase 1: Parallel Research

```python
def _research_one(item):
    org = item['org']
    url, text, notes = research_org(org['association_name'], org['org_type'], org.get('email', ''))
    return item['key'], (url, text, notes)

with ThreadPoolExecutor(max_workers=8) as executor:
    futures = {executor.submit(_research_one, item): item for item in work_items}
    for future in as_completed(futures):
        item = futures[future]
        key, result = future.result()
        research_results[key] = result
```

**`as_completed()` not `wait()`:** Results are processed as they finish, not in order. This means if org #7's website loads in 2 seconds and org #1's takes 30 seconds, org #7's result is available immediately. The `futures` dict maps futures back to items so we know which result belongs to which org.

```python
status_icon = '✓' if url else '✗'
print(f'{status_icon} {org["association_name"]} ...')
```

Visual feedback: ✓ means a website was found, ✗ means no website (the LLM will work from org name alone).

#### Phase 2: Sequential Generation

```python
for item in work_items:
    website_url, website_text, research_notes = research_results.get(key, ('', '', ''))
    subject, body = generate_email_for_org(org, website_url, website_text, research_notes)
    time.sleep(0.5)
```

Sequential because each LLM call takes 5-15 seconds and API rate limits prevent parallelism. The 0.5s sleep between calls is conservative - prevents hitting rate limits even on free tier.

---

## `smn_prompt.py` - AI Instructions

### CAMPAIGN dict
```python
CAMPAIGN = {
    "name": "#SpeakingMyName",
    "sheet_tab": "#SpeakingMyName Outreach",
}
```

Used by `generate_smn_emails.py` for sheet tab naming.

### SYSTEM_PROMPT - Key sections

**Follow-up vs fresh logic (lines ~70-75):**
The prompt has two complete behavior modes. If `FOLLOW-UP: YES` appears in the user prompt, the AI acknowledges prior contact. If `FOLLOW-UP: NO`, it gives a full campaign introduction. This dual-mode design means one prompt file handles both scenarios.

**Org-type personalization rules (lines ~40-55):**
Separate guidance for immigrant-serving orgs, youth/education orgs, healthcare orgs, cultural orgs, and government/civic orgs. Each type gets a specific angle for why name inclusion matters to their community.

**Greeting rules (lines ~80-88):**
```python
# In the prompt:
- If the contact has a first name, use "Hi Lisa,"
- If multiple people: "Hi James and Shavone,"
- If NO named contact: use org name - "Hi Queens Community House Team,"
- NEVER: "Dear Partnerships Team" or "To whom it may concern"
```

This is critical for the SMN campaign because many tracker entries don't have individual contact names - they have organization-level emails. Using the org name in the greeting ("Hi Queens Community House Team,") makes the email feel personalized even without a named recipient.
