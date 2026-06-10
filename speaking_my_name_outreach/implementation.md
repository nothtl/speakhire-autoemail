# #SpeakingMyName — Code Walkthrough & Maintenance Guide

## File dependency map

```
speaking_my_name_outreach/
│
├── generate_smn_emails.py   ← run locally: python generate_smn_emails.py
│   ├── reads:  _data/#SpeakingMyName Outreach Tracker(1).xlsx
│   ├── writes: Google Sheet → "#SpeakingMyName Outreach" tab (columns A-L)
│   ├── calls:  DeepSeek/OpenRouter API (LLM) — one call per org
│   ├── scrapes: org websites via BeautifulSoup + requests (parallel, 8 workers)
│   └── imports from: ../speakhire-outreach/speakhire-outreach-simple/ (.env, gspread)
│
├── smn_send.js              ← paste into Google Sheets → Extensions → Apps Script
│   ├── reads:  "#SpeakingMyName Outreach" tab (columns A-K)
│   ├── sends:  GmailApp.sendEmail() with HTML body + tracking pixel
│   ├── writes: Status (col F), Sent At (col K), Opens/Clicks/Last Open/Last Click
│   ├── sender: "Hana Figueroa from SpeakHire"
│   └── adds:   "#SpeakingMyName" menu (Preview, Send Test, Batch 1/2, Sync Tracking, Sync Dashboard)
│
└── _data/
    └── #SpeakingMyName Outreach Tracker(1).xlsx   ← manual org tracker (tab: OrgsCompaniesAssociations)
```

## End-to-end flow: what happens when you run `python generate_smn_emails.py`

```
1. Script starts
   ├── Reads .env from ../speakhire-outreach/speakhire-outreach-simple/.env
   │   └── Gets: GOOGLE_SHEET_URL, GOOGLE_APPLICATION_CREDENTIALS, DEEPSEEK_API_KEY
   ├── Reads _data/#SpeakingMyName Outreach Tracker(1).xlsx
   │   └── Tab: "OrgsCompaniesAssociations"
   │   └── Headers in row 2, data starts row 3
   │   └── For each row: extracts Full Name, Title, Association Name, Type, Email,
   │       Reached Out?, Response, Notes
   │   └── Determines is_followup = (Reached Out == "Yes" AND Response == "Pending")
   │
2. Connects to Google Sheet
   ├── gspread.service_account(CREDS_PATH) → authenticates
   ├── Gets or creates "#SpeakingMyName Outreach" tab
   │   └── If new: writes headers (A1:L1), freezes row 1, bolds headers
   │
3. Imports orgs from xlsx → sheet
   ├── Reads existing sheet rows, builds a lookup by org name (lowercased)
   ├── For each xlsx org not already in sheet: appends a new row
   │   └── Status = "READY", Follow-up = Yes/No
   │
4. PHASE 1: Research (parallel — ThreadPoolExecutor, 8 workers)
   ├── For each org that needs generation:
   │   └── research_org(org_name, org_type, email_hint)
   │       ├── search_org_website() — 3 strategies to find the website
   │       │   ├── Strategy 0: extract domain from email (skip gmail/yahoo/etc)
   │       │   │   └── GET https://{email_domain} → valid? → return URL
   │       │   ├── Strategy 1: domain pattern guessing
   │       │   │   └── GET www.{clean_name}.org, {clean_name}.org, www.{clean_name}nyc.org
   │       │   │   └── Fast: timeout=2s, check len(response) > 500
   │       │   └── Strategy 2: Bing search fallback
   │       │       └── GET bing.com/search?q="{org_name}" site:.org OR site:.com
   │       │       └── Extract first non-social result from h2 > a tags
   │       ├── fetch_url_text(website_url) — scrape homepage
   │       │   └── BeautifulSoup → strip script/style/nav/footer/header
   │       │   └── Clean text[:5000] chars
   │       ├── fetch_url_text() on up to 10 sub-pages
   │       │   └── Paths: /about, /mission, /programs, /diversity, /inclusion, /impact, etc.
   │       ├── DuckDuckGo search for DEI/inclusion programs
   │       │   └── GET html.duckduckgo.com/html/?q="{org_name}" diversity equity inclusion
   │       └── _extract_key_info(combined_text)
   │           └── Keyword scan: sentences mentioning org name + mission keywords
   │           └── Returns top 5-8 relevant sentences, deduplicated by prefix
   │   └── Writes research notes to sheet column L
   │
5. PHASE 2: Generate emails (sequential — LLM rate limits)
   └── For each org:
       ├── generate_email_for_org(org, website_url, website_text, research_notes)
       │   ├── Builds user prompt with:
       │   │   ├── Org details (name, type, contact person, title)
       │   │   ├── Follow-up status + prior contact context (if applicable)
       │   │   ├── Internal notes (if any)
       │   │   └── Research findings (website text + DEI results)
       │   ├── call_llm(SMN_SYSTEM_PROMPT, user_prompt)
       │   │   ├── POST to DeepSeek or OpenRouter /chat/completions
       │   │   ├── temperature=0.7, timeout=90s
       │   │   ├── Rate limited? → exponential backoff: 3s, 6s, 12s
       │   │   ├── Parses JSON from response (strips ``` markers, extracts {...})
       │   │   └── Falls back to aggressive ASCII-only parse if JSON decode fails
       │   └── Returns (subject, body) — both cleaned of em dashes, smart quotes, fake-casual tics
       └── Writes to sheet: Full Name, Title, Org, Type, Email, Status=DRAFTED,
           Notes, Subject, Body, Follow-up, Research Notes → all 12 columns
       └── sleep(0.5) between LLM calls
```

## Code walkthrough: generate_smn_emails.py (837 lines)

### Imports and path setup (lines 1-36)

```python
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..', 'speakhire-outreach', 'speakhire-outreach-simple'))
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..', 'speakhire-outreach'))
```

**Why these paths:** The script lives in `speaking_my_name_outreach/` but needs access to:
- `speakhire-outreach/speakhire-outreach-simple/` — for `.env`, `gspread`, `requests`
- `speakhire-outreach/` — reserved for potential shared modules

The `..` goes up to the repo root, then into `speakhire-outreach/`. This is a relative import — works from any machine as long as the folder structure is preserved.

### Config (lines 38-58)

```python
XLSX_PATH = os.path.join(SCRIPT_DIR, '#SpeakingMyName Outreach Tracker(1).xlsx')
XLSX_TAB  = 'OrgsCompaniesAssociations'

SHEET_URL = os.getenv('GOOGLE_SHEET_URL')
CREDS_PATH = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
GSHEET_TAB = '#SpeakingMyName Outreach'
COL_RESEARCH = 12  # L

# LLM: prefer OpenRouter free model, fall back to DeepSeek
OPENROUTER_KEY = os.getenv('OPENROUTER_API_KEY', '')
if OPENROUTER_KEY:
    API_KEY = OPENROUTER_KEY
    BASE_URL = 'https://openrouter.ai/api/v1'
    LLM_MODEL = 'google/gemma-4-31b-it:free'
else:
    API_KEY = os.getenv('DEEPSEEK_API_KEY')
    BASE_URL = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
    LLM_MODEL = 'deepseek-chat'
```

**Why OpenRouter first:** The free Gemma model on OpenRouter costs $0 per call. If `OPENROUTER_API_KEY` is set in `.env`, it's used. Otherwise falls back to DeepSeek (paid, but cheap). This makes testing free.

### SMN_SYSTEM_PROMPT (lines 63-132)

This is the largest prompt in the codebase. Key sections with line references:

| Lines | Section | What it controls |
|---|---|---|
| 63-77 | Campaign intro | How the campaign is described to the LLM. The June 16th date, the three steps, existing partners |
| 78-81 | Partner commitment | What you're asking partners to DO (share, encourage, be recognized) |
| 82-91 | **Personalization rules** | The core quality control. Rules 4-8 tell the LLM how to connect name inclusion to different org types (immigrant-serving, youth/education, health/wellness, cultural/community, government/civic). Rule 9 is the banned phrases list |
| 92-97 | Tone rules | Under 180 words, one exclamation point max, no rhetorical questions |
| 98-100 | Follow-up vs fresh | Different intro lines and structure depending on prior contact |
| 102-105 | Greeting rules | "Hi Lisa," not "Dear Lisa". Multi-person: "Hi James and Shavone,". No named contact → use ORG name: "Hi Queens Community House Team," |
| 107-116 | Sender + intro | Hana Figueroa, Campaign Coordinator. Different intro lines for fresh vs follow-up |
| 117-126 | Subject line rules | Must include org name + "#SpeakingMyName" + hint at their community. Under 12 words. Examples provided |

**How to change the sender name:** Edit line 108 and update `DEFAULT_SENDER_NAME` in `smn_send.js` line 34.

**How to change the campaign date:** Search for "June 16" in the prompt (appears in lines 65, 66, 78, 100) and replace. Also update the default subject fallback in `smn_send.js` line 120.

### read_orgs_from_xlsx() (lines 472-519)

```python
def read_orgs_from_xlsx():
    wb = openpyxl.load_workbook(XLSX_PATH)
    ws = wb[XLSX_TAB]
    headers = [safe_str(cell.value) for cell in ws[2]]  # row 2 = headers (row 1 = section title)
    orgs = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=3, values_only=True), 3):
        vals = [safe_str(v) for v in row]
        # Extract: full_name(0), title(1), association_name(2), org_type(3),
        #          contact_type(4), email(5), reached_out(6), date(7),
        #          response(8), notes(9)
        # Skip: empty rows, "n/a", "none", "test"
        is_followup = (reached_out.lower() == 'yes' and response.lower() == 'pending')
        orgs.append({...})
    return orgs
```

**Why row 2 for headers:** The xlsx has a section title in row 1 ("Organizations / Companies / Associations"). Actual column headers are in row 2. Data starts in row 3.

**Why `safe_str()`:** Google Sheets stores empty cells as `NaN` floats. `safe_str()` converts NaN → empty string, None → empty string, everything else → stripped string.

### import_orgs_to_sheet() (lines 570-602)

Deduplicates by org name (lowercased). Only imports orgs NOT already in the sheet. Sets initial status to "READY" (the generate phase changes this to "DRAFTED" after LLM generation).

### search_org_website() (lines 177-255)

Three-strategy website finder:

**Strategy 0 — Email domain (lines 182-196):**
```python
if email_hint and '@' in email_hint:
    email_domain = email_hint.split('@')[-1].strip().lower()
    skip_email_domains = ['gmail.com', 'yahoo.com', 'hotmail.com', ...]
    if email_domain not in skip_email_domains:
        for prefix in ['https://www.', 'https://']:
            r = requests.get(f'{prefix}{email_domain}', timeout=(1, 2), ...)
            if r.status_code < 400 and len(r.text) > 300:
                return r.url
```

Most orgs have their domain as their email domain (e.g. `jane@queenscommunityhouse.org` → `queenscommunityhouse.org`). This is the most reliable strategy. The timeout is short (1-2s) because we're just checking if the domain resolves.

**Strategy 1 — Domain patterns (lines 198-229):**
Cleans the org name (removes "Inc", "LLC", punctuation), then tries:
- `www.{clean}.org`
- `{clean}.org`
- `www.{clean}nyc.org`

Only tries these 3 (fast). More aggressive patterns were unreliable.

**Strategy 2 — Bing search (lines 232-255):**
Scrapes Bing results (because Bing's HTML is easier to parse than Google's). Filters out social media, job sites, and directory sites.

### _extract_key_info() (lines 258-285)

Keyword-based sentence extraction. Two matching strategies:
1. Sentence contains org name + any mission keyword (diversity, equity, inclusion, belonging, immigrant, youth, etc.)
2. Sentence contains multiple keyword categories (mission keywords + community keywords)

Deduplicates by first 40 characters. Returns top 8 unique sentences.

### call_llm() (lines 386-439)

```python
def call_llm(system_prompt, user_prompt, max_retries=3):
    headers = {'Authorization': f'Bearer {API_KEY}', 'Content-Type': 'application/json'}
    if OPENROUTER_KEY:
        headers['HTTP-Referer'] = 'https://speakhire.org'
        headers['X-Title'] = 'SpeakHire #SpeakingMyName Outreach'

    for attempt in range(max_retries):
        resp = requests.post(f'{BASE_URL}/chat/completions', headers=headers, json={
            'model': LLM_MODEL,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'temperature': 0.7,
        }, timeout=90)
        if resp.status_code == 429:
            wait = (2 ** attempt) * 3  # 3s, 6s, 12s
            time.sleep(wait)
            continue
        resp.raise_for_status()
        break

    content = resp.json()['choices'][0]['message']['content'].strip()
    # Clean markdown wrappers
    if content.startswith('```'): content = content[content.find('\n'):].strip()
    if content.endswith('```'): content = content[:-3].strip()
    # Extract JSON
    s, e = content.find('{'), content.rfind('}')
    if s != -1 and e != -1: content = content[s:e+1]
    # Sanitize invisible chars
    content = ''.join(c for c in content if ord(c) >= 32 or c in '\n\r\t')
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        content = ''.join(c for c in content if 32 <= ord(c) <= 126 or c in '\n\r')
        return json.loads(content)
```

**Why temperature=0.7:** High enough for creative personalization, low enough to stay on-topic and follow the JSON format.

**Why two JSON parse attempts:** The first strips invisible Unicode (keeps printable). The second strips to ASCII-only. Some LLMs inject zero-width spaces or non-breaking spaces that break `json.loads()`.

**Why exponential backoff:** DeepSeek's free tier rate-limits aggressively. 3s → 6s → 12s retry gives the server time to reset. After 3 failures, the exception propagates up and the org is skipped.

### clean_email() (lines 455-465)

```python
def clean_email(text):
    text = text.replace('—', '-').replace('–', '-')  # em/en dashes
    text = text.replace('‘', "'").replace('’', "'")   # smart quotes
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace('…', '...')                       # ellipsis
    text = re.sub(r', right\?', '?', text)                # fake-casual tic
    text = re.sub(r'\bright\?\b', '', text)
    text = re.sub(r', you know\?', '?', text)
    return text.strip()
```

LLMs love em dashes and rhetorical "right?" endings. These feel like AI-generated text. This function strips them all. The `\bright\?\b` regex catches standalone "right?" that some models sprinkle in.

### main() (lines 667-836)

The orchestration function. Key flow:

1. `parse_args()` — handles `--preview`, `--import-only`, `--research-only`, `--rows`, `--row`, `--force`
2. `read_orgs_from_xlsx()` → `get_sheet()` → `import_orgs_to_sheet()`
3. Builds work_items list: orgs with status not in (DRAFTED, APPROVED, SENT), or forced
4. **Phase 1 (parallel):** `ThreadPoolExecutor(max_workers=8)` runs `research_org()` on all work items. Results stored in `research_results` dict keyed by org name. Research notes written to sheet column L. If `--research-only`, stops here.
5. **Phase 2 (sequential):** Loops through work items, calls `generate_email_for_org()`, writes results to sheet. `sleep(0.5)` between calls for rate limiting.

**Why parallel research but sequential generation:** Research is I/O-bound (HTTP requests to websites) — parallel makes it 8x faster. Generation is LLM-rate-limited — sequential with sleeps is required to avoid 429 errors.

## Code walkthrough: smn_send.js (230+ lines with tracking addons)

### Config section (lines 29-34)

```javascript
var BATCH_START = 2;      // Row 2 = first org (row 1 = header)
var BATCH_SIZE  = 50;     // Gmail free: 100 emails/day limit, so 50/batch is safe
var SHEET_NAME  = "#SpeakingMyName Outreach";
var DEFAULT_SENDER_NAME = "Hana Figueroa from SpeakHire";
```

### Column mapping (lines 40-50)

```javascript
var COL_FULL_NAME       = 1;   // A
var COL_TITLE           = 2;   // B
var COL_ASSOC_NAME      = 3;   // C
var COL_TYPE            = 4;   // D
var COL_EMAIL           = 5;   // E
var COL_STATUS          = 6;   // F
var COL_NOTES           = 7;   // G
var COL_EMAIL_SUBJECT   = 8;   // H
var COL_PERSONALIZED    = 9;   // I
var COL_FOLLOWUP        = 10;  // J
var COL_SENT_AT         = 11;  // K
```

**Must stay in sync with `generate_smn_emails.py`** which writes to these exact positions. If you add a column to the sheet, update both files.

### sendBatch() (lines 56-165)

```javascript
function sendBatch() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  var lastRow = sheet.getLastRow();
  var data = sheet.getRange(1, 1, lastRow, COL_SENT_AT).getValues();

  for (var i = BATCH_START - 1; i < endRow; i++) {
    var row = data[i];
    var status = String(row[COL_STATUS - 1] || "").trim();
    var email  = String(row[COL_EMAIL - 1]   || "").trim();
    var body   = String(row[COL_PERSONALIZED - 1] || "").trim();
    // ... skip checks ...

    // Build HTML from plain text + tracking pixel
    var htmlBody = body
      .replace(/&/g, "&amp;")         // escape HTML entities
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\n/g, "<br>");        // line breaks → <br>
    htmlBody += getTrackingPixel(email, fullName, assocName, CAMPAIGN_SLUG);
    htmlBody = '<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;">' +
               htmlBody + '</div>';

    GmailApp.sendEmail(email, subject, body, {   // body = plain text fallback
      htmlBody: htmlBody,                         // htmlBody = with pixel
      name: DEFAULT_SENDER_NAME,
    });
    // Mark SENT, write timestamp, sleep 1s
  }
}
```

**Line-by-line explanation of the HTML conversion:**
- `replace(/&/g, "&amp;")` — must be FIRST. If you escape `&` after escaping `<`, you'd double-escape. Order: `&` → `<` → `>` → `\n`.
- `replace(/\n/g, "<br>")` — converts plain text line breaks to HTML. Email clients need `<br>` tags; plain `\n` is ignored in HTML mode.
- `getTrackingPixel()` appends the invisible 1×1 image. This is what records opens.
- The wrapper `<div>` sets a clean font stack. Without it, Gmail defaults to Times New Roman.
- `GmailApp.sendEmail(email, subject, body, {htmlBody, name})` — passes BOTH plain text and HTML. Clients that prefer plain text (rare) get `body`. Clients that render HTML get `htmlBody` with the tracking pixel.

### Skip checks (lines 85-121)

In order of evaluation:
1. `status === "SENT"` → skip (counts as "skipped")
2. `status !== "DRAFTED"` → skip (silently — these are rows awaiting generation)
3. No email or invalid email → mark as ERROR
4. No body → mark as ERROR
5. No subject → use default: `"{Org Name} + #SpeakingMyName on June 16"`

### previewBatch() (lines 169-227)

Identical logic to `sendBatch()` but:
- Doesn't call `GmailApp.sendEmail()`
- Collects preview info into an array
- Shows first 15 rows in an alert dialog
- Counts "would send" vs "would skip"

### onOpen() (lines 257-268)

Creates the menu when the sheet opens. Google Sheets runs `onOpen()` automatically. Menu items are bound to functions — clicking "Send Batch 1" calls `sendBatch1()`.

### Tracking functions (from sheet_addons.js integration)

`encodeTrackingId()`, `getTrackingPixel()`, `syncTracking()`, `syncDashboard()` — these are described in detail in `email_tracking/implementation.md`. They're pasted into this script to provide the 🔄 Sync Tracking and 📊 Sync Dashboard menu items.

## How the two phases are coordinated

```
Phase 1 (parallel)                    Phase 2 (sequential)
══════════════════                    ═════════════════════
research_org("QC House") ─┐           generate_email_for_org("QC House", research)
research_org("LGBT Net")  ─┤  wait    generate_email_for_org("LGBT Net", research)
research_org("IMPACCT")   ─┤   for    generate_email_for_org("IMPACCT", research)
research_org("Coalition") ─┘   all    generate_email_for_org("Coalition", research)
                                  │
                          Research written to    │  Emails written to
                          sheet column L         │  sheet columns H, I
                          (via ws.update)        │  (via ws.update)
```

Phase 2 can start as soon as its org's research is done — but the current implementation waits for all research to complete before starting generation. This is a design simplification; making it truly pipelined (start generation as soon as research for that org is done) would be faster but more complex.

## Maintenance tasks

### How to add a new org type's personalization rule

In `SMN_SYSTEM_PROMPT`, rule section (lines 82-91), add a new numbered rule:

```
10. For environmental/sustainability orgs: tie name pronunciation to environmental
    justice — communities whose names are mispronounced are often the same communities
    disproportionately affected by environmental issues.
```

The LLM will use this pattern when the org type matches.

### How to change the campaign from June 16 to a new date

Search the entire file for "June 16" and replace all occurrences. Also update:
1. Line 65: "goes LIVE on June 16th (next Monday — just 7 days away)" — update both date and day count
2. Line 78: "on or before June 16th"
3. Line 100: "the June 16th campaign date is now just days away"
4. `smn_send.js` line 120: default subject fallback

### How to add a new column to both the sheet and the scripts

1. Decide what the column is (e.g., "M: Second Contact")
2. In `generate_smn_emails.py`, update the write call (line 815) to include the new field
3. In `smn_send.js`, add `var COL_SECOND_CONTACT = 12; // L`
4. Update `COL_SENT_AT` to `13` (it shifts right)
5. In the xlsx, add the column header to row 2
6. In `read_orgs_from_xlsx()`, read the new column value

### How to regenerate an org's email after editing the prompt

1. In the Google Sheet, change that org's `Status` from `DRAFTED`/`SENT` back to `READY`
2. Run `python generate_smn_emails.py --force --row {row_number}`
3. The `--force` flag overrides the "already DRAFTED" skip

## Common issues

### "No website found" for an org

1. Verify the org name spelling matches their domain (spaces → run together: "Queens Community House" → should resolve to `queenscommunityhouse.org`)
2. Add an org email (not gmail) — Strategy 0 extracts the domain from the email
3. Manually find the website, enter it in the Research Notes column (L), and the LLM will use that context even without scraping
4. If all strategies fail, the LLM prompt includes: "(No website content available. Use what you know about this organization from its name, type, and mission area...)" — the LLM does its best from general knowledge

### LLM generates generic/unpersonalized emails

1. Check the Research Notes column (L) in the sheet — if empty, the scraper found nothing
2. The org type might not match any personalization rule — add a rule for that type
3. Try `--research-only` first to verify the scraper is finding good content
4. Reduce temperature from 0.7 to 0.5 for more focused output (but less creative personalization)

### Follow-up emails sound like fresh outreach

The `is_followup` flag is set in `read_orgs_from_xlsx()`: `reached_out.lower() == 'yes' AND response.lower() == 'pending'`. Both must be true. Check the xlsx values — the column names must be "Reached Out?" (column 7) and "Response" (column 9).

### Sheet creates duplicate rows

The dedup key is `org['association_name'].lower().strip()`. If you import the same org with slightly different spelling ("QC House" vs "Queens Community House"), it creates a duplicate. Fix the name in the xlsx before importing, or delete the duplicate row from the sheet manually.

### Rate limited during generation

The script retries 3 times with exponential backoff (3s, 6s, 12s). If it still fails:
1. Reduce `max_workers` in `ThreadPoolExecutor` from 8 to 4 (line 749)
2. Increase `time.sleep(0.5)` to `1.0` between generation calls (line 828)
3. Switch from DeepSeek to OpenRouter (Gemma free tier) which has different rate limits
4. Run with `--rows 10` to process in smaller chunks

### Windows encoding issues (non-Latin characters)

Lines 22-26 handle this:
```python
if not isinstance(sys.stdout, io.TextIOWrapper) or sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
```
This reconfigures stdout to UTF-8 on Windows (where the default is often cp1252). If you still see garbled characters, the issue is likely in the terminal, not the script.

## Testing

```bash
# Step 1: Verify xlsx reads correctly (no API calls)
python generate_smn_emails.py --preview

# Step 2: Import orgs to sheet without LLM
python generate_smn_emails.py --import-only

# Step 3: Research orgs and store findings (no email generation)
python generate_smn_emails.py --research-only

# Step 4: Generate 1 test email
python generate_smn_emails.py --row 2

# Step 5: Review in the sheet, then send test
# In Apps Script: run sendTest()

# Step 6: If test email looks good, generate the rest
python generate_smn_emails.py --rows 10   # first 10
python generate_smn_emails.py             # all remaining

# Step 7: Send in batches from the sheet menu
```

For the send script specifically:
1. Run `previewBatch()` — verify it shows the right emails
2. Run `sendTest()` — sends row 2 to yourself
3. Inspect the raw email (Gmail → Show Original) — search for `azurewebsites.net/api/o/` to verify the tracking pixel is present
4. Open the email, wait 30 seconds, run `syncTracking()` — should show Opens: 1
