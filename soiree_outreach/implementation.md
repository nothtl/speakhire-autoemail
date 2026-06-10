# Soirée Outreach — Code Walkthrough & Maintenance Guide

## File dependency map

```
soiree_outreach/
│
├── generate_soiree_emails.py   ← run locally: python generate_soiree_emails.py --csv FILE --type TYPE
│   ├── reads:  CSV file (sponsors or attendees) — auto-detects columns
│   ├── writes: Google Sheet → "Soiree Outreach" tab (columns A-L)
│   ├── calls:  DeepSeek/OpenRouter API (LLM) — one call per contact
│   ├── scrapes: org websites via BeautifulSoup + requests (sponsor type only)
│   └── imports: soiree_prompt.py (system prompts + event facts),
│                ../speakhire-outreach/speakhire-outreach-simple/ (.env, gspread)
│
├── soiree_send.js              ← paste into Google Sheets → Extensions → Apps Script
│   ├── reads:  "Soiree Outreach" tab (columns A-L)
│   ├── sends:  GmailApp.sendEmail() with HTML body + tracking pixel
│   ├── writes: Status (col F), Sent At (col K)
│   ├── sender: "Hana from SpeakHire"
│   └── adds:   "Soiree Outreach" menu (Preview, Send Test, Batch 1/2, Sync Tracking, Sync Dashboard)
│
└── soiree_prompt.py            ← imported by generate_soiree_emails.py
    ├── defines: SOIREE_DATE, SOIREE_TIME, SOIREE_VENUE, SOIREE_TAGLINE
    ├── defines: SOIREE_HIGHLIGHTS (list of 6)
    ├── defines: SOIREE_SPONSOR_TIERS (list of 4 tier tuples)
    ├── defines: SPONSOR_SYSTEM_PROMPT (large f-string, ~90 lines)
    ├── defines: INDIVIDUAL_SYSTEM_PROMPT (large f-string, ~50 lines)
    └── exposes: get_prompt(campaign_type) → returns the right system prompt
```

## End-to-end flow: what happens when you run the script

```
1. Script starts
   ├── Parses CLI args: --csv (required), --type (required: sponsor|individual),
   │                    --rows (optional), --preview (optional)
   ├── Sets up sys.path to find ../speakhire-outreach/speakhire-outreach-simple/
   │   └── Loads .env: GOOGLE_SHEET_URL, GOOGLE_APPLICATION_CREDENTIALS, DEEPSEEK_API_KEY
   │
2. read_csv(filepath)
   ├── Opens CSV with utf-8-sig encoding (handles BOM)
   ├── Auto-detects column headers (case-insensitive, multiple alias matching)
   │   ├── Name: 'name', 'full name', 'Name', 'Full Name', 'first name' + 'last name'
   │   ├── Email: 'email', 'Email', 'EMAIL', 'contact email', 'Contact Email'
   │   ├── Org: 'organization', 'company', 'org_name', 'association name', ...
   │   ├── Title: 'title', 'job title', 'position', ...
   │   └── Profile: 'languages', 'career interests', 'career field', 'notes'
   ├── Skips rows where both name AND org are empty
   └── Returns list of contact dicts
   │
3. If --preview: prints contacts and exits (no API calls)
   │
4. get_sheet()
   ├── gspread.service_account(CREDS_PATH) → open_by_key(sheet_id) → worksheet("Soiree Outreach")
   └── If tab doesn't exist: creates it with 12 headers (A-L), freezes row 1, bolds headers
   │
5. For each contact:
   ├── [sponsor only] research_org(contact['org'], contact['email'])
   │   ├── search_org_website() — email domain → pattern guessing → return URL
   │   └── fetch_url_text() — scrape homepage, keyword-extract mission sentences
   │       └── Builds research_notes string (URL, title, top 5 key sentences)
   │
   ├── build_user_prompt(contact, args.type, website_url, research_text)
   │   ├── sponsor: includes company research block + sponsorship tiers
   │   └── individual: includes profile block (job, company, career field, languages, interests)
   │
   ├── call_llm(system_prompt, user_prompt)
   │   ├── POST to DeepSeek/OpenRouter /chat/completions
   │   ├── temperature=0.7, timeout=90s
   │   ├── Rate limited (429) → exponential backoff: 3s, 6s, 12s
   │   ├── Parses JSON response (strips ``` markers, extracts {...})
   │   └── Returns dict with email_subject + email_draft
   │
   └── ws.update() — writes all 12 columns to the sheet row
       └── For individuals: extra column (L) gets languages, interests, career field
       └── Status set to DRAFTED
       └── sleep(0.5) between contacts
```

## Code walkthrough: generate_soiree_emails.py (442 lines)

### Imports and path setup (lines 1-36)

```python
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..', 'speakhire-outreach', 'speakhire-outreach-simple'))
sys.path.insert(0, SCRIPT_DIR)
```

**Why two paths:**
- `../speakhire-outreach/speakhire-outreach-simple/` — for `.env` loading, `gspread`, `requests`
- `SCRIPT_DIR` (soiree_outreach/) — so that `from soiree_prompt import ...` works (Python needs the current directory on the path)

The `load_dotenv()` call uses a hardcoded absolute path on line 33. If you move the project, update this path.

### Config (lines 38-70)

```python
SHEET_URL = os.getenv('GOOGLE_SHEET_URL')
CREDS_PATH = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
GSHEET_TAB = 'Soiree Outreach'

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

**Why OpenRouter first:** Free tier (Gemma model) costs $0. DeepSeek is the paid fallback. Testing with `--rows 2` on OpenRouter = completely free.

### Column constants (lines 59-70)

```python
COL_NAME        = 1   # A: Contact Name
COL_TITLE       = 2   # B: Job Title
COL_ORG         = 3   # C: Organization/Company
COL_EMAIL       = 4   # D: Email
COL_TYPE        = 5   # E: Campaign Type (sponsor/individual)
COL_STATUS      = 6   # F: Status
COL_NOTES       = 7   # G: Notes / Profile
COL_SUBJECT     = 8   # H: Email Subject
COL_BODY        = 9   # I: Personalized Email
COL_RESEARCH    = 10  # J: Research Notes
COL_SENT_AT     = 11  # K: Sent At
COL_EXTRA       = 12  # L: Extra (languages, interests, etc.)
```

**Must match `soiree_send.js`** column constants exactly. Both read/write to these same positions.

### read_csv() (lines 77-128)

The most flexible CSV reader in the codebase. Key design:

```python
name = row.get('name', row.get('full name', row.get('Name', row.get('Full Name',
       row.get('first name', row.get('First Name', '')))))).strip()
# Also handles separate first/last name columns
first = row.get('first name', row.get('First Name', row.get('first_name', ''))).strip()
last = row.get('last name', row.get('Last Name', row.get('last_name', ''))).strip()
if first and last:
    name = f"{first} {last}"
```

**Why cascading `.get()`:** Different CSV sources use different column names. This chain tries 6 common variants for "name" before giving up. Same pattern for email, org, title, languages, interests, career field.

**Why first+last merge:** Some CSVs have separate first/last name columns instead of a full name column. The script handles both.

**The `next()` pattern for email:**
```python
email_cols = ['email', 'Email', 'EMAIL', 'contact email', 'Contact Email']
email = next((row.get(c, '').strip() for c in email_cols if row.get(c, '').strip()), '')
```
Uses a generator + `next()` to find the first non-empty column match. More concise than cascading `.get()`.

### search_org_website() (lines 157-183)

Simpler than the SMN version — only two strategies:

**Strategy 0 — Email domain:**
```python
if email_hint and '@' in email_hint:
    email_domain = email_hint.split('@')[-1].strip().lower()
    skip = ['gmail.com', 'yahoo.com', 'hotmail.com', ...]
    if email_domain not in skip:
        for prefix in ['https://www.', 'https://']:
            r = requests.get(f'{prefix}{email_domain}', timeout=(1, 2), ...)
            if r.status_code < 400 and len(r.text) > 300:
                return r.url
```
Fast check (1-2s timeout). Most org emails use the org domain.

**Strategy 1 — Domain patterns:**
```python
clean = re.sub(r'[^a-z0-9\s]', '', org_name.lower().strip())
clean = re.sub(r'\s+', '', clean)
for url in [f"https://www.{clean}.org", f"https://{clean}.org", f"https://www.{clean}.com"]:
    r = requests.get(url, timeout=(1, 2), ...)
    if r.status_code < 400 and len(r.text) > 300:
        return r.url
```
Only 3 attempts (fast). No Bing fallback — if these fail, the LLM gets no website data and works from the org name alone.

### fetch_url_text() (lines 135-154)

```python
def fetch_url_text(url, timeout=10):
    result = {"url": url, "title": "", "text": "", "error": ""}
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(url, timeout=timeout, headers=_BOT_HEADERS, allow_redirects=True)
        resp.raise_for_status()
        result["url"] = resp.url  # final URL after redirects
        soup = BeautifulSoup(resp.text, "html.parser")
        t = soup.find("title")
        if t: result["title"] = t.get_text(strip=True)
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        result["text"] = soup.get_text(separator=" ", strip=True)[:5000]
    except Exception as e:
        result["error"] = str(e)[:200]
    return result
```

**Never crashes** — all exceptions caught, returned in the `error` field. The caller checks `result["error"]` before using the text.

**Why `result["url"] = resp.url`:** After redirects, the actual URL may differ from what was requested (e.g., `http://example.org` → `https://www.example.org/`). We store the final URL for accuracy.

### build_user_prompt() (lines 299-347)

Two completely different prompt builders depending on campaign type:

**Sponsor prompt:**
```python
return f"""Write a personalized Soiree SPONSORSHIP email for:

COMPANY: {org}
CONTACT: {name} {f'({title})' if title else ''}
NOTES: {notes}

COMPANY RESEARCH:
{research_text}

The Soiree is on {SOIREE_DATE} at {SOIREE_VENUE}. Tiers: $5K–$50K+.
CRITICAL: Reference at least ONE specific program, initiative, or fact about {org}."""
```

**Individual prompt:**
```python
profile = []
if title: profile.append(f"Job: {title}")
if org: profile.append(f"Company: {org}")
if career_field: profile.append(f"Career field: {career_field}")
if languages: profile.append(f"Languages: {languages}")
if interests: profile.append(f"Interests: {interests}")
profile_text = '\n'.join(profile) if profile else '(minimal profile — write a warm, brief invitation)'

return f"""Write a personalized Soiree INVITATION for:

NAME: {name}
EMAIL: {email}
{profile_text}
NOTES: {notes}

CRITICAL: Make this feel personal to {name}. Use whatever details are available."""
```

**Why different prompts:** Sponsors need org research + tier information. Individuals need profile-based personalization + a warmer tone. The system prompt (from `soiree_prompt.py`) also differs — sponsor prompt is ~90 lines, individual is ~50 lines.

### call_llm() (lines 225-261)

Same pattern as SMN's `call_llm()`:
1. POST to LLM API with system + user messages
2. On 429 → exponential backoff (3s, 6s, 12s)
3. Parse JSON response (strip ``` markers, extract {...})
4. Two-pass sanitize: invisible Unicode → ASCII-only fallback

### main() (lines 353-441)

```python
def main():
    contacts = read_csv(args.csv)
    if args.rows: contacts = contacts[:args.rows]
    if args.preview:  # print and exit

    ws = get_sheet()  # connect to Google Sheet
    system_prompt = get_prompt(args.type)  # sponsor or individual prompt

    for i, contact in enumerate(contacts):
        # Research (sponsor only)
        if args.type == 'sponsor' and contact.get('org'):
            website_url, research_text, research_notes = research_org(...)

        # Generate
        user_prompt = build_user_prompt(contact, args.type, website_url, research_text)
        result = call_llm(system_prompt, user_prompt)
        subject = clean(result.get('email_subject', ''))
        body = clean(result.get('email_draft', ''))

        # Write to sheet (all 12 columns)
        ws.update(values=[[...12 values...]], range_name=f'A{sheet_row}')
        generated += 1
        time.sleep(0.5)
```

**Key difference from SMN:** No two-phase (research then generate). Research and generation happen in one loop per contact. For sponsors, research runs first, then immediately the LLM call uses that research. This is simpler but slower for large lists (no parallel research).

## Code walkthrough: soiree_prompt.py (166 lines)

### Event facts (lines 11-32)

```python
SOIREE_DATE      = "Wednesday, June 24th, 2026"
SOIREE_TIME      = "5:30 PM – 9:00 PM EDT"
SOIREE_VENUE     = "Salesforce Tower, Ohana Floor (41F), 1095 6th Ave, New York"
SOIREE_TAGLINE   = "An evening powering immigrant and first-gen careers."
SOIREE_TICKET    = "https://www.zeffy.com/en-US/ticketing/speakhire-soiree"

SOIREE_HIGHLIGHTS = [
    "41st-floor skyline views from the Salesforce Tower Ohana Floor",
    "Food and drinks included",
    "VIP pre-event reception for sponsors and special guests",
    "Stories from SpeakHire youth whose careers were launched through our programs",
    "Networking with 200+ professionals, corporate leaders, and community partners",
    "Live showcase of the #SpeakingMyName campaign",
]

SOIREE_SPONSOR_TIERS = [
    ("SKY TIER",    "$50,000+", "Presenting sponsor — keynote..."),
    ("FOREST TIER", "$25,000+", "VIP table for 8..."),
    ("RAY TIER",    "$10,000+", "Reserved table for 6..."),
    ("RIVER TIER",  "$5,000+",  "Reserved seating for 4..."),
]
```

**To update for next year:** Change these facts and everything flows through. Both system prompts use f-string interpolation, so the new values appear in LLM prompts automatically.

### SPONSOR_SYSTEM_PROMPT (lines 40-99)

A ~90-line f-string that defines the LLM's behavior for sponsor emails. Key sections:
- **Lines 42-56:** Soirée description — date, venue, highlights, what the money funds
- **Lines 58-62:** Sponsorship tiers (interpolated from `SOIREE_SPONSOR_TIERS`)
- **Lines 66-71:** Personalization rules — MUST find a specific named thing from the company's website (program, initiative, DEI report, CSR commitment)
- **Lines 73-76:** Tone rules — under 200 words, warm and human, CTA is a 15-20 minute call
- **Lines 78-81:** Sender identity — "Hana, Partnerships Lead at SpeakHire"
- **Lines 83-99:** JSON output format

### INDIVIDUAL_SYSTEM_PROMPT (lines 106-156)

A ~50-line f-string for personal invites. Key sections:
- **Lines 118-126:** Personalization rules — use their specific details naturally (languages, career field, job title). Examples provided for healthcare, tech, student, senior professional.
- **Lines 126-127:** Banned phrases — "we would be honored," "your unique perspective," "exciting opportunity," "don't miss out"
- **Lines 130-133:** Tone — personal, warm, casual-professional, under 150 words
- **Lines 136-139:** Sender identity — "Hana, Community Engagement at SpeakHire" (different title than sponsor emails!)

### get_prompt() (lines 159-165)

```python
def get_prompt(campaign_type: str) -> str:
    if campaign_type == "sponsor":
        return SPONSOR_SYSTEM_PROMPT
    elif campaign_type == "individual":
        return INDIVIDUAL_SYSTEM_PROMPT
    else:
        raise ValueError(f"Unknown campaign type: {campaign_type}")
```

Simple dispatcher. The ValueError is caught nowhere — it intentionally crashes the script so you know immediately if you typo'd the `--type` flag.

## Code walkthrough: soiree_send.js (125 lines + tracking addons)

### Config (lines 16-19)

```javascript
var BATCH_START = 2;
var BATCH_SIZE  = 50;
var SHEET_NAME  = "Soiree Outreach";
var DEFAULT_SENDER_NAME = "Hana from SpeakHire";
```

### Column mapping (lines 22-32)

```javascript
var COL_NAME       = 1;   // A: Contact Name
var COL_TITLE      = 2;   // B: Job Title
var COL_ORG        = 3;   // C: Organization
var COL_EMAIL      = 4;   // D: Email
var COL_TYPE       = 5;   // E: Campaign Type (sponsor/individual)
var COL_STATUS     = 6;   // F: Status
var COL_NOTES      = 7;   // G: Notes
var COL_SUBJECT    = 8;   // H: Email Subject
var COL_BODY       = 9;   // I: Personalized Email
var COL_RESEARCH   = 10;  // J: Research Notes
var COL_SENT_AT    = 11;  // K: Sent At
```

Note: 12 columns in the sheet (A-L), but the send script only needs 11 (column L "Extra" is informational, not used for sending).

### sendBatch() (lines 38-87)

Identical pattern to `smn_send.js` sendBatch():
1. Read sheet data
2. For each row: skip SENT, skip not DRAFTED, validate email + body
3. Build HTML from plain text + append tracking pixel
4. `GmailApp.sendEmail()` with both plain text and HTML
5. Mark SENT + timestamp, sleep 1s

**Default subject fallback (line 64):**
```javascript
if (!subject) { subject = "SpeakHire Soiree — June 24 at Salesforce Tower"; }
```
Update this when the event date/venue changes.

### previewBatch() (lines 89-108)

Same dry-run pattern. Shows first 15 rows in an alert.

### onOpen() (lines 114-125)

Creates "Soiree Outreach" menu.

## How the dual campaign type system works

The same `.py` and `.js` files handle both sponsors and individuals:

```
--type sponsor                    --type individual
══════════════                    ══════════════════
get_prompt("sponsor")             get_prompt("individual")
  → SPONSOR_SYSTEM_PROMPT           → INDIVIDUAL_SYSTEM_PROMPT
  → Formal, tier-focused            → Warm, personal, ticket-focused

build_user_prompt()               build_user_prompt()
  → Company research block          → Profile block (job, interests, languages)

research_org() runs              research_org() SKIPPED
  → Scrapes company website         → Uses CSV profile data instead

Sheet col E = "sponsor"          Sheet col E = "individual"
```

The `--type` flag is the switch. Everything downstream branches on it. The JS send script doesn't care about type — it sends whatever is in the sheet regardless of sponsor/individual.

## Maintenance tasks

### How to update event details for next year

Change these in `soiree_prompt.py`:
1. `SOIREE_DATE` (line 11)
2. `SOIREE_TIME` (line 12)
3. `SOIREE_VENUE` (line 13)
4. `SOIREE_TAGLINE` (line 14)
5. `SOIREE_TICKET` (line 15) — update the Zeffy link
6. `SOIREE_HIGHLIGHTS` (lines 18-25) — update for new venue features
7. `SOIREE_SPONSOR_TIERS` (lines 27-32) — update amounts and benefits

Then update the default subject fallback in `soiree_send.js` line 64.

### How to add a new sponsorship tier

In `soiree_prompt.py`, add to the `SOIREE_SPONSOR_TIERS` list:
```python
("OCEAN TIER", "$2,500+", "Reserved seating for 2, name in program, social media mention"),
```
The tuple format is `(name, amount, description)`. Both system prompts reference this list via f-string, so the new tier appears automatically in LLM-generated emails.

### How to change the sender for a campaign type

In `soiree_prompt.py`:
- Sponsor sender: edit line 79 ("Hana, Partnerships Lead at SpeakHire")
- Individual sender: edit line 137 ("Hana, Community Engagement at SpeakHire")

Also update `DEFAULT_SENDER_NAME` in `soiree_send.js` if the first name changes.

### How to add a new CSV column for personalization

1. In `read_csv()` (lines 77-128), add detection for the new column name
2. Add the value to the contact dict
3. In `build_user_prompt()` (lines 299-347), include the new field in the profile block
4. In `main()` (line 412-418), include it in the `extra` column (L)

Example — adding "college":
```python
# In read_csv():
college_cols = ['college', 'College', 'university', 'University', 'school']
college = next((row.get(c, '').strip() for c in college_cols if row.get(c, '').strip()), '')
contacts.append({..., 'college': college})

# In build_user_prompt() (individual):
if college: profile.append(f"School: {college}")

# In main() (extra column):
if contact.get('college'): extras.append(f"School: {contact['college']}")
```

## Common issues

### "Unknown type" error

`--type` must be exactly `sponsor` or `individual`. The check is case-sensitive: `choices=['sponsor', 'individual']` in argparse.

### Empty body after LLM generation

The LLM returns `email_draft` in the JSON. If the LLM returns `email_body` instead (some models use different key names), the result is empty. Check the raw LLM response by adding `print(result)` after `call_llm()`.

### Sponsor contact has no organization field

For `--type sponsor`, the CSV MUST have an organization/company column. Without it, `research_org()` can't find a website. The script will still generate (using just the name) but the email will be generic.

### CSV with non-Latin characters (accents, Chinese, etc.)

The script opens the CSV with `encoding='utf-8-sig'`. If characters still appear garbled:
1. Open the CSV in Notepad
2. Save As → set Encoding to "UTF-8 with BOM"
3. Re-run

### Date-sensitive content

The Soirée prompt uses hardcoded dates. If you run the script after the event date, the LLM will still generate emails inviting people to a past event. Always update `SOIREE_DATE` before running.

## Testing

```bash
# Step 1: Preview contacts (no API calls)
python generate_soiree_emails.py --csv sponsors.csv --type sponsor --preview

# Step 2: Test with 2 rows
python generate_soiree_emails.py --csv sponsors.csv --type sponsor --rows 2

# Step 3: Test individual invites
python generate_soiree_emails.py --csv attendees.csv --type individual --rows 2

# Step 4: Review in sheet, then send test
# In Apps Script: sendTest() → sends row 2

# Step 5: If good, generate all
python generate_soiree_emails.py --csv sponsors.csv --type sponsor
```

For the send script: `sendTest()` sends row 2 only. Verify the email arrives with tracking pixel (Gmail → Show Original → search for `azurewebsites.net`), then `syncTracking()` to confirm the open was recorded.
