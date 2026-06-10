# General Outreach — Code Walkthrough & Maintenance Guide

## File dependency map

```
speakhire-outreach/
│
├── generate.py                          ← run locally: python generate.py
│   ├── reads:  Google Sheet → "Outreach Tracker" tab (37-column schema)
│   ├── writes: same tab (STATUS, EMAIL_DRAFT, EVIDENCE_*, PERSONALISED_OPENER, etc.)
│   ├── calls:  LLM via outreach_worker.py, web search if configured
│   ├── imports: _data/campaign_prompts.py (prompts per campaign type)
│   ├── imports: speakhire-outreach-simple/outreach_worker.py (core engine)
│   └── patches: worker.SYSTEM_PROMPT at runtime (campaign-specific prompt per row)
│
├── outreach_send.js                     ← paste into Google Sheets → Extensions → Apps Script
│   ├── reads:  "Outreach Tracker" tab (37-column schema)
│   ├── sends:  GmailApp.sendEmail() with HTML body + tracking pixel
│   ├── priority: HUMAN_EDITED_DRAFT (col J) > EMAIL_DRAFT (col I)
│   ├── detects: sender name from SEND_FROM (col AG) or SENDER_NAME (col K)
│   └── adds:   "SpeakHire Outreach" menu (Preview, Send Test, Batch 1-3, Sync Tracking, Sync Dashboard)
│
├── speakhire-outreach-simple/           ← shared dependency (used by ALL campaigns)
│   ├── .env                             ← GOOGLE_SHEET_URL, API keys (NEVER commit)
│   ├── autoemail-speakhire-*.json       ← Google service account credentials (NEVER commit)
│   ├── outreach_worker.py               ← core engine: research + LLM + sheet I/O + FastAPI (1796 lines)
│   └── requirements.txt                 ← pandas, gspread, requests, pydantic, fastapi, etc.
│
└── _data/
    └── campaign_prompts.py              ← prompt + sender definitions for sponsor/partner/individual
```

## How this folder differs from the other campaigns

This is the **general-purpose engine**. Unlike the other campaigns (which have dedicated scripts), this one:

1. **Uses a shared worker module** (`outreach_worker.py`) that runs as both a CLI tool AND a FastAPI server
2. **Patches the worker at runtime** — `generate.py` doesn't have its own prompts; it swaps `worker.SYSTEM_PROMPT` per row based on campaign type
3. **Has a 37-column schema** — the most complex sheet, with columns for evidence tracking, sender management, email provider status, and follow-up
4. **Can send via multiple providers** — Gmail SMTP (free), SendGrid (paid), or the JS Apps Script (free, Gmail API)
5. **Supports local xlsx mode** — can run entirely offline without Google Sheets

## End-to-end flow: `python generate.py`

```
1. Script loads
   ├── Sets up sys.path to find _data/ and speakhire-outreach-simple/
   ├── Loads .env (searches speakhire-outreach-simple/ → speakhire-outreach-shared/ → .)
   ├── Imports campaign_prompts (get_prompt, get_sender, CAMPAIGN_TYPES)
   └── Imports outreach_worker (the core engine)
   │
2. read_rows()
   ├── gspread.service_account(CREDS_PATH) → open_by_key(sheet_id)
   ├── Gets "Outreach Tracker" worksheet (creates if missing, with 37 headers)
   ├── get_all_records(expected_headers=ROW_COLUMNS)
   └── Returns list of row dicts, each with _row (sheet row number)
   │
3. For each row where STATUS = READY_FOR_RESEARCH:
   │
   ├── Validate CAMPAIGN_TYPE (must be in [sponsor, partner, individual])
   │
   ├── PATCH worker module (runtime prompt swap):
   │   ├── worker.SYSTEM_PROMPT = get_prompt(campaign)     ← from campaign_prompts.py
   │   ├── worker.DEFAULT_SENDER_NAME = sender["name"]
   │   ├── worker.DEFAULT_SENDER_ORG = sender["org"]
   │   └── worker.get_sender_title = lambda r: clean(r.get("SENDER_TITLE")) or sender["title"]
   │
   ├── generate_for_row(row):
   │   ├── [has ORG_WEBSITE] worker.research(org_name, org_website)
   │   │   ├── crawl_org_website(website)
   │   │   │   ├── fetch_url_text(homepage) → extract visible text + internal links
   │   │   │   ├── _score_link() on each internal link
   │   │   │   │   └── +3 for /about, /mission, /programs, etc.
   │   │   │   │   └── -len(path)/20 (shorter paths = more important)
   │   │   │   └── Fetch up to 5 best subpages → combine into 12000 char text
   │   │   └── [has search API] search_web() → Serper/Tavily/SerpAPI
   │   │
   │   ├── worker.generate_draft(org_name, recipient, ...)
   │   │   ├── Attempt 1: LangChain structured output (if installed)
   │   │   │   └── llm.with_structured_output(OutreachDraft)
   │   │   │   └── Returns Pydantic-validated dict → no manual JSON parsing
   │   │   ├── Attempt 2: Direct HTTP + JSON parse
   │   │   │   └── _call_llm_direct() → requests.post → json.loads()
   │   │   └── Attempt 3: _fallback_draft() — hardcoded template
   │   │       └── Used when: no API key configured, or all attempts fail
   │   │
   │   └── validate_evidence(draft, search_results, website_text, org_name)
   │       └── If confidence=HIGH: verify source_url exists in research
   │       └── If evidence is missing → downgrade to MEDIUM
   │
   └── Write back to row: STATUS=DRAFTED, EVIDENCE_*, EMAIL_DRAFT, PERSONALISED_OPENER, etc.
   │
4. write_rows(rows, changed_indices)
   └── Batch-updates only changed columns for changed rows
   └── Uses ws.batch_update() for efficiency (one API call for all cells)
```

## Code walkthrough: generate.py (296 lines)

### Path setup (lines 19-29)

```python
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKER_DIR = os.environ.get("OUTREACH_WORKER_DIR",
    os.path.join(SCRIPT_DIR, "speakhire-outreach-simple"))
if not os.path.isdir(WORKER_DIR):
    WORKER_DIR = os.path.join(SCRIPT_DIR, "speakhire-outreach-shared")

sys.path.insert(0, SCRIPT_DIR)       # for: from campaign_prompts import ...
sys.path.insert(0, WORKER_DIR)       # for: import outreach_worker as worker
sys.path.insert(0, os.path.join(SCRIPT_DIR, '_data'))  # for: campaign_prompts (moved to _data/)
```

**Why three paths:**
1. `SCRIPT_DIR` — so `from campaign_prompts import ...` works (the module is in `_data/` which is added next)
2. `WORKER_DIR` — so `import outreach_worker as worker` works (the engine)
3. `_data/` — campaign_prompts.py lives here after the folder reorganization

**Why `OUTREACH_WORKER_DIR` env var:** Allows overriding the worker location without editing code. Useful if you have multiple worker versions (dev/prod).

### The prompt patching pattern (lines 168-176, 203)

```python
# Save originals
_orig_get_sender_title = worker.get_sender_title

# Patch for this row's campaign type
worker.SYSTEM_PROMPT = get_prompt(campaign)
worker.DEFAULT_SENDER_NAME = sender["name"]
worker.DEFAULT_SENDER_ORG = sender["org"]
worker.get_sender_title = lambda r: worker.clean(r.get("SENDER_TITLE")) or sender["title"]

# ... generate draft ...

# Restore originals
worker.get_sender_title = _orig_get_sender_title
```

**Why patching instead of passing parameters:** `outreach_worker.py` is also a standalone FastAPI server. It uses module-level `SYSTEM_PROMPT` for its default behavior. Rather than refactoring every function to accept a prompt parameter, `generate.py` temporarily swaps the module variable. This is a pragmatic tradeoff — slightly hacky but much less code than threading prompt parameters through 5 function calls.

**Why restore after each row:** The next row might have a different campaign type. Without restoring, a `sponsor` row followed by an `individual` row would use the sponsor prompt for the individual email.

### read_rows() (lines 104-127)

```python
def read_rows() -> List[Dict]:
    ws = _get_worksheet()
    data = ws.get_all_records(expected_headers=ROW_COLUMNS)
    for i, rd in enumerate(data):
        r = {}
        for c in ROW_COLUMNS:
            val = rd.get(c, "")
            if val is None or (isinstance(val, float) and str(val) == "nan"):
                val = ""
            r[c] = str(val).strip()
        # Derive RECIPIENT from CONTACT_FIRST_NAME + CONTACT_LAST_NAME if empty
        if not r.get("RECIPIENT", ""):
            first = r.get("CONTACT_FIRST_NAME", "")
            last = r.get("CONTACT_LAST_NAME", "")
            if first and last: r["RECIPIENT"] = f"{first} {last}"
        r["_row"] = i + 2  # row 1 = header, data[0] = row 2
        rows.append(r)
    return rows
```

**Why `expected_headers`:** `gspread.get_all_records()` with `expected_headers` ensures all 37 columns exist in the returned dicts, even if the sheet header row has fewer columns. Missing columns get empty string values — no KeyError.

**Why derive RECIPIENT:** Some rows have separate first/last name columns but no combined RECIPIENT field. This derivation normalizes the data so downstream code always has a recipient name.

### write_rows() (lines 130-142)

Only writes back rows that actually changed (`changed_indices`). Uses `ws.update(f"A{rn}", [row_data])` — one API call per changed row. For small batches (<50 rows), this is fast enough. For larger batches, `ws.batch_update()` in `outreach_worker.py` is used instead.

### generate_for_row() (lines 149-230)

The per-row orchestration function. Key flow:
1. Validate campaign type
2. Patch worker module with campaign-specific prompt + sender
3. Call `worker.research()` → `worker.generate_draft()`
4. Map LLM output fields to sheet column names via `field_map`
5. Set STATUS = "ERROR" if draft has errors, otherwise "DRAFTED"

The `field_map` (lines 206-220) translates lowercase LLM field names to uppercase sheet column names:
```python
field_map = {
    "RESEARCH_QUERY": "research_query",
    "EVIDENCE_TITLE": "evidence_title",
    "EMAIL_SUBJECT": "email_subject",
    "EMAIL_DRAFT": "email_draft",
    ...
}
```

## Code walkthrough: outreach_worker.py (1796 lines)

This is the largest file in the project. Here are the critical sections:

### ROW_COLUMNS (lines 129-146) — the 37-column schema

```python
ROW_COLUMNS = [
    "ORG_NAME","ORG_WEBSITE","RECIPIENT","EMAIL",         # A-D: Identity
    "STATUS","NOTES",                                      # E-F: Status
    "PERSONALISED_OPENER","EMAIL_SUBJECT","EMAIL_DRAFT",   # G-I: Draft content
    "HUMAN_EDITED_DRAFT",                                  # J: Manual edits
    "SENDER_NAME","OPT_OUT",                               # K-L: Sender + skip flag
    "SENT_AT","ERROR",                                     # M-N: Send tracking
    "CONTACT_FIRST_NAME","CONTACT_LAST_NAME",              # O-P: Contact details
    "CONTACT_PAGE_URL","ORG_TYPE","SEGMENT","TAGS","PRIORITY", # Q-U: Classification
    "RESEARCH_QUERY","EVIDENCE_TITLE","EVIDENCE_SUMMARY",  # V-X: Research output
    "SOURCE_URL","SOURCE_DATE","RELEVANT_THEME","EVIDENCE_CONFIDENCE", # Y-AB
    "CTA_TYPE","CALL_DURATION",                            # AC-AD: Call to action
    "SENDER_TITLE","SENDER_ORG","SEND_FROM",               # AE-AG: Sender details
    "EMAIL_PROVIDER_STATUS","FOLLOW_UP_DATE","FOLLOW_UP_STATUS", # AH-AJ
    "LAST_UPDATED",                                        # AK
]
```

This is the single source of truth for the sheet structure. Every read and write uses this list.

### DROP_DOWN_VALUES (lines 153-162)

```python
DROP_DOWN_VALUES = {
    "STATUS": ["READY_FOR_RESEARCH","DRAFTED","SENDING","SENT","SKIP","ERROR"],
    "ORG_TYPE": ["nonprofit","school","university","company","association",...],
    "SEGMENT": ["immigrant community","youth empowerment","education","DEI",...],
    "PRIORITY": ["HIGH","MEDIUM","LOW"],
    "EVIDENCE_CONFIDENCE": ["HIGH","MEDIUM","LOW"],
    ...
}
```

These are applied as data validation dropdowns in the Google Sheet via `_apply_dropdowns_and_formatting()`. The function creates in-cell dropdown menus for each of these columns, plus conditional formatting (green for SENT, red for ERROR, yellow for NEEDS_REVIEW).

### crawl_org_website() (lines 871-926)

The primary research method (free, always available):

```python
def crawl_org_website(website, contact_page=""):
    main_page = fetch_url_text(start_url)          # fetch homepage
    candidates = [(link, _score_link(link)) for link in main_page["links"]]
    candidates.sort(key=lambda x: x[1], reverse=True)  # sort by relevance score
    # Fetch up to 5 best subpages
    for link, score in candidates[:8]:
        if len(result["pages"]) >= 6: break
        page = fetch_url_text(link["url"])
        result["pages"].append(page)
    # Combine: homepage text + "\n---\n" + subpage1 + ...
    result["combined_text"] = "\n\n---\n\n".join(texts)[:12000]
```

**Why 5 subpages max:** More pages = more research quality but slower. 5 is the sweet spot — covers /about, /mission, /programs, /impact, and one more, which is enough for most orgs.

**Why 12,000 char limit:** LLM context windows have limits. 12,000 chars of website text + the system prompt + user prompt still fits in most models' context (DeepSeek: 64K tokens, Gemma: 128K tokens).

### _score_link() (lines 855-868)

Scores internal links for relevance:
```python
def _score_link(link):
    url_lower = link["url"].lower() + link["text"].lower()
    score = 0
    for pat in _USEFUL_PATH_PATTERNS:  # "about", "mission", "programs", etc.
        if pat in url_lower:
            score += 3
    path = urlparse(link["url"]).path
    score -= len(path) // 20          # shorter paths = more important
    if len(link["text"]) > 3:         # descriptive link text
        score += 2
    return score
```

Mission-critical pages (/about, /programs) score higher than blog posts or contact pages.

### generate_draft() (lines 1261-1349)

Three-tier fallback system:
1. **LangChain structured output** — `llm.with_structured_output(OutreachDraft)` — returns a Pydantic-validated dict directly. Most reliable, but requires `langchain-openai` installed.
2. **Direct HTTP + JSON parse** — `_call_llm_direct()` → `json.loads()` → `OutreachDraft(**raw)` for validation. Works without langchain.
3. **_fallback_draft()** — hardcoded template email. Used when no API key is configured or all LLM attempts fail.

### validate_evidence() (lines 1143-1189)

Prevents the LLM from inventing evidence:
```python
def validate_evidence(draft, search_results, website_text, org_name):
    if conf != "HIGH": return draft           # only validate HIGH claims

    if not source_url:                        # must have a URL
        draft["evidence_confidence"] = "MEDIUM"; return draft
    if not evidence_title:                    # must have a title
        draft["evidence_confidence"] = "MEDIUM"; return draft

    # URL must appear in search results or website text
    found_in_search = any(source_url in su for su in search_urls)
    found_in_website = source_domain in website_text.lower()
    if not found_in_search and not found_in_website:
        draft["evidence_confidence"] = "MEDIUM"  # downgrade
    return draft
```

This is the quality gate. If the LLM says "HIGH confidence" but can't point to a URL that actually exists in the research, it gets downgraded to MEDIUM. The result: the sheet's EVIDENCE_CONFIDENCE column can be trusted.

### send_approved() (lines 1535-1576)

Python-based email sending (alternative to the JS script). Supports:
- **Gmail SMTP** (free, up to 500 emails/day): `smtplib.SMTP_SSL("smtp.gmail.com", 465)`
- **SendGrid** (paid, reliable): `SendGridAPIClient(SENDGRID_API_KEY).send(msg)`
- **Dry run mode**: logs what would be sent without actually sending

```python
def _send(to_email, to_name, subject, body, dry_run, display_from=""):
    if dry_run:
        return {"status":"DRY_RUN", "message":f"Would have sent to {to_email}"}

    if USE_GMAIL:
        # Gmail SMTP: smtplib.SMTP_SSL → login → sendmail
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, [to_email], msg.as_string())
    else:
        # SendGrid API
        SendGridAPIClient(SENDGRID_API_KEY).send(msg)
```

### FastAPI app (lines 1638-1752)

The worker can run as a web server:
```python
app = FastAPI(title="SpeakHire Outreach API", version="2.0.0")

@app.post("/generate-ready-drafts")   # trigger draft generation from Apps Script
@app.post("/auto-approve")            # approve HIGH confidence drafts
@app.post("/send-approved")           # send approved emails
@app.get("/health")                   # health check
```

The API is protected by `X-API-Key` header (checked against `SHEETS_WEBHOOK_API_KEY` env var). Apps Script webhooks can call these endpoints for server-side processing.

## Code walkthrough: campaign_prompts.py (in _data/)

Defines per-campaign-type prompts and senders. Three campaign types supported:

```python
CAMPAIGN_TYPES = ["sponsor", "partner", "individual"]

def get_prompt(campaign_type):
    # Returns the system prompt for that type (large f-string)

def get_sender(campaign_type):
    # Returns {"name": "Hana", "title": "Partnerships Lead", "org": "SpeakHire"}
```

**To add a new campaign type:**
1. Add a new prompt function to this file
2. Add the type name to `CAMPAIGN_TYPES`
3. Add a sender profile to `get_sender()`
4. Add it to `DROP_DOWN_VALUES["STATUS"]` in outreach_worker.py (line 154) so it appears in the sheet dropdown

## Code walkthrough: outreach_send.js (360+ lines)

### Key difference from other send scripts

This script handles the most complex email building logic:

**Body priority (line 61-65):**
```javascript
function getEmailBody(humanEdited, aiDraft) {
  var body = (humanEdited || "").trim();
  if (body) return body;           // HUMAN_EDITED_DRAFT takes priority
  return (aiDraft || "").trim();   // fall back to AI draft
}
```

**Sender name priority (lines 71-77):**
```javascript
function getSenderName(senderName, sendFrom) {
  var name = (sendFrom || "").trim();     // SEND_FROM (col AG) — highest priority
  if (name) return name;
  name = (senderName || "").trim();       // SENDER_NAME (col K)
  if (name) return name + " from SpeakHire";
  return DEFAULT_SENDER_NAME;             // hardcoded fallback
}
```

**Signature detection (lines 84-106):**
```javascript
function buildEmailBody(body, senderName) {
  var hasSignOff = /\b(Best,?|Sincerely,?|Warmly,?|Cheers,?|Thank you,?|Thanks,?)\s*$/m.test(body);
  if (!hasSignOff) {
    if (!/\b(Hana|Alicia|Tingli)\b/.test(body.slice(-40))) {
      body += "\n\nBest,\n" + senderName.replace(" from SpeakHire", "") + "\nSpeakHire";
    }
  }
  return body;
}
```

This checks if the AI draft already ends with a sign-off. If not (and if the sender's name isn't already in the last 40 characters), it appends the SpeakHire signature block. This prevents double-signatures.

### sendBatch() (lines 112-233)

Same pattern as other scripts but with the email building pipeline:
1. `getEmailBody(humanEdit, aiDraft)` → pick the right body
2. `getSenderName(senderName, sendFrom)` → pick the right sender
3. `buildEmailBody(body, displayName)` → ensure signature
4. Convert to HTML + append tracking pixel
5. `GmailApp.sendEmail()` with both plain text and HTML
6. Mark SENT + timestamp + `EMAIL_PROVIDER_STATUS = gmail_ok`

### The 37-column read (lines 125-126)

```javascript
var lastCol = COL_EMAIL_PROVIDER;  // 34 (column AH)
var data = sheet.getRange(1, 1, lastRow, lastCol).getValues();
```

Reads up to column 34 (AH) — the widest column the send script needs. This is more efficient than reading all 37 columns.

## Maintenance tasks

### How to add a new campaign type

1. In `_data/campaign_prompts.py`: add a new prompt + sender
2. In `outreach_worker.py` line 154: add to `DROP_DOWN_VALUES["STATUS"]`
3. In `generate.py`: add to `CAMPAIGN_TYPES` import
4. The new type appears in the sheet dropdown automatically

### How to add a new column to the 37-column schema

This requires changes in **four files**:

1. **`outreach_worker.py` line 129:** Add column name to `ROW_COLUMNS`
2. **`outreach_worker.py` line 164:** Add to `OUTPUT_COLUMNS` if the system writes to it
3. **`outreach_worker.py` line 404:** Add mapping in `_norm_hdr()` if importing from old sheets
4. **`generate.py` line 206:** Add to `field_map` if the LLM output maps to it
5. **`outreach_send.js`:** Add `COL_NEW_FIELD = 37` if the send script reads it

The order in `ROW_COLUMNS` determines the sheet column order. Insert at the right position — the column letter shifts for everything to the right.

### How to change the default LLM model

In `.env` (inside `speakhire-outreach-simple/`):
```bash
LLM_PROVIDER=deepseek           # or openai
MODEL_NAME=deepseek-chat        # or gpt-4o, claude-fable-5, etc.
DEEPSEEK_API_KEY=sk-...
```

The worker reads these at startup. Restart after changing.

### How to enable web search

In `.env`:
```bash
SEARCH_PROVIDER=serper           # or tavily, serpapi
SERPER_API_KEY=your-key-here
```

Without this, only website crawling is used for research. With it, additional search results supplement the website data.

### How to use the Python-based email sender (instead of JS)

In `.env`:
```bash
USE_GMAIL=true
GMAIL_USER=hana@speakhire.org
GMAIL_APP_PASSWORD=abcd efgh ijkl mnop    # Google App Password, not your real password
DRY_RUN=false
```

Then run:
```bash
python outreach_worker.py --send
```

This is useful for automation (cron job, CI/CD) where Apps Script manual clicking isn't practical.

## Common issues

### "No LLM API key set — using fallback draft"

The fallback produces a generic template email. To fix: set `DEEPSEEK_API_KEY` or `OPENAI_API_KEY` in `.env`.

### Row stuck at READY_FOR_RESEARCH

`generate.py` only processes rows with exactly this status. After generation, it becomes `DRAFTED`. To re-generate: change status back to `READY_FOR_RESEARCH` manually in the sheet and re-run.

### "No research source configured"

The row has no `ORG_WEBSITE` AND no search API configured. Either:
- Add a website URL to column B
- Or configure `SEARCH_PROVIDER=serper` + `SERPER_API_KEY` in `.env`

### LangChain import error (not critical)

LangChain is optional. Without it, the script uses direct HTTP calls + manual JSON parsing. Structured output is more reliable but not required. Install with: `pip install langchain-openai langchain-core`.

### Evidence confidence always LOW

The LLM isn't finding good evidence. Check:
1. `ORG_WEBSITE` is set and reachable
2. The website actually has mission/program content (some sites are sparse)
3. The search API is configured for supplementary results
4. If the org is small with a minimal website, LOW confidence is correct — the LLM shouldn't invent evidence

### Large sheet — slow performance

With 500+ rows, reading/writing the full sheet is slow. Solutions:
1. Use `--row N` to process single rows
2. Split the sheet into multiple tabs by campaign type
3. Use the FastAPI server mode for background processing

## Testing

```bash
# Process a single row (best first test)
python generate.py --row 2

# Force re-generate an already-drafted row (after editing prompt)
python generate.py --row 2 --force

# Run the FastAPI server locally
uvicorn outreach_worker:app --reload
curl http://localhost:8000/health

# Auto-approve HIGH confidence drafts
python outreach_worker.py --approve

# Dry-run send (preview what would be sent)
python outreach_worker.py --send --dry-run true
```

For the JS send script:
1. `previewBatch()` — see what rows would be sent
2. `sendTest()` — row 2 only, to yourself
3. Verify the email: tracking pixel present, signature correct, sender name right
4. `sendBatch1()` — first 50 rows
