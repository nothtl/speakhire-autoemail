# #SpeakingMyName Outreach

Generates and sends heavily-personalized partnership emails for the **#SpeakingMyName campaign** — a campaign where people record short videos sharing their name, pronunciation, and story. The campaign goes **LIVE on June 16th**.

Each email ties the organization's SPECIFIC mission and programs to why name inclusion matters for THEIR community.

## Files

| File | Purpose |
|---|---|
| `generate_smn_emails.py` | **Generate** — reads orgs from the Excel tracker, researches each org's website, calls the LLM to write personalized emails, writes them to the Google Sheet |
| `smn_send.js` | **Send** — Google Apps Script that sends all DRAFTED emails from the sheet via Gmail (no manual approval needed) |
| `_data/` | Supporting data — contains the Excel tracker with orgs |

## How to use

### Step 1 — The data source

Orgs are stored in `_data/#SpeakingMyName Outreach Tracker(1).xlsx` (tab: "OrgsCompaniesAssociations"). The script reads columns: Full Name, Title, Association Name, Type, Email, plus outreach tracking fields (Reached Out?, Date, Response, Notes).

### Step 2 — Import & generate emails

```bash
# Preview orgs without calling the LLM
python generate_smn_emails.py --preview

# Import orgs from xlsx into Google Sheet only (no LLM)
python generate_smn_emails.py --import-only

# Research orgs and store findings (no email generation)
python generate_smn_emails.py --research-only

# Generate emails for the first 3 orgs (test)
python generate_smn_emails.py --rows 3

# Generate for a specific xlsx row
python generate_smn_emails.py --row 5

# Generate all remaining
python generate_smn_emails.py
```

The script does two phases:
1. **Phase 1 (parallel):** Researches all orgs — finds their website via email domain extraction + domain pattern guessing, scrapes homepage + mission/about pages + DEI search results
2. **Phase 2 (sequential):** Calls the LLM for each org, generates a personalized email, writes `Status = DRAFTED` to the sheet

### Step 3 — Send emails

The send script sends **all DRAFTED rows directly** (no manual approval step).

1. Open your Google Sheet
2. Go to **Extensions > Apps Script**
3. Paste the contents of `smn_send.js`
4. Run `onOpen()` once to create the "#SpeakingMyName" menu, or run directly:
   - `sendTest()` — sends 1 test email (row 2)
   - `sendBatch1()` — rows 2–51
   - `sendBatch2()` — rows 52–101
   - `previewBatch()` — dry run, shows what WOULD send

The script sends rows where `Status = DRAFTED`, skips `SENT` rows, and sets `Status = SENT` with timestamp after sending.

### Google Sheet tab: "#SpeakingMyName Outreach"

| Col | Field | Description |
|---|---|---|
| A | Full Name | Contact person's name |
| B | Title | Their job title |
| C | Association Name | Organization name |
| D | Type | Org type (nonprofit, school, etc.) |
| E | Contact Email | Email address |
| F | Status | `DRAFTED` → `SENT` |
| G | Notes | Internal notes |
| H | Email Subject | AI-generated subject (includes org name + #SpeakingMyName) |
| I | Personalized Email | AI-generated body |
| J | Follow-up? | `Yes` or `No` |
| K | Sent At | Timestamp |
| L | Research Notes | Website + DEI research findings |

## How the code works step-by-step

### `generate_smn_emails.py`

1. **Load config** — reads `.env`, sets up LLM (OpenRouter free Gemma or DeepSeek), connects to Google Sheets via `gspread`
2. **`read_orgs_from_xlsx()`** — opens the Excel tracker, reads all orgs from the "OrgsCompaniesAssociations" tab, determines if each is a follow-up (Reached Out = Yes + Response = Pending)
3. **`import_orgs_to_sheet()`** — imports new orgs into the Google Sheet "SpeakingMyName Outreach" tab (skips duplicates by org name)
4. **Phase 1 — `research_org()` (parallel, 8 workers):**
   - **`search_org_website()`** — three strategies to find the website:
     1. Extract domain from email (if it's not gmail/yahoo/etc.)
     2. Domain pattern guessing (`www.{clean_name}.org`, `{clean_name}.org`, `www.{clean_name}nyc.org`)
     3. Bing search fallback
   - **`fetch_url_text()`** — scrapes a webpage with BeautifulSoup, strips nav/footer/scripts, returns title + cleaned text (5,000 chars)
   - Fetches homepage + up to 10 mission sub-pages (`/about`, `/mission`, `/programs`, `/diversity`, etc.)
   - Searches DuckDuckGo for DEI/inclusion programs
   - **`_extract_key_info()`** — scans all text for mission-relevant sentences using keyword matching
   - Returns website URL, research text (for LLM), and research notes (for sheet column L)
5. **Phase 2 — `generate_email_for_org()` (sequential):**
   - Builds the user prompt with org details, follow-up status, prior contact context, internal notes, and research findings
   - **`call_llm()`** — sends the SMN_SYSTEM_PROMPT (detailed rules for personalization, tone, greetings, subject lines) + user prompt. Handles rate limiting with exponential backoff. Parses JSON response
   - **`clean_email()`** — strips LLM artifacts (em dashes, smart quotes, fake-casual tics like "right?")
   - Writes subject, body, and research notes to the sheet row
6. Both phases honor `--rows`, `--row`, `--force`, `--research-only` flags

### The LLM Prompt (`SMN_SYSTEM_PROMPT`)

The system prompt is the secret sauce — it defines:
- **Personalization rules:** Must reference at least ONE specific program by name. Must tie the org's mission to why name pronunciation matters for THEIR community. Different connection strategies for different org types (immigrant, youth, health, cultural, government)
- **Tone rules:** Under 180 words, mission-driven, warm, specific. One exclamation point max
- **Greeting rules:** Uses first name ("Hi Lisa," not "Dear Lisa"). Falls back to org name if no contact person
- **Subject line rules:** Must include org name + "#SpeakingMyName" + hint at their community, under 12 words
- **Follow-up vs fresh:** Different intro lines and structure depending on whether this is a follow-up
- **Banned phrases:** "we would be honored," "your commitment to diversity and inclusion," "exciting opportunity," etc.

### `smn_send.js`

1. **`onOpen()`** — creates the "#SpeakingMyName" menu with Preview, Test, and Batch options
2. **`sendBatch()`**:
   - Reads the "#SpeakingMyName Outreach" tab
   - For each row: skips if `SENT`, skips if not `DRAFTED`, errors on missing email/body
   - No subject? Uses default: `"{Org Name} + #SpeakingMyName on June 16"`
   - Sends via `GmailApp.sendEmail()` with sender `"Hana Figueroa from SpeakHire"`
   - Marks row as `SENT` with timestamp, sleeps 1 second between sends
   - Shows a summary alert when done
3. **`previewBatch()`** — same logic without sending; shows up to 15 preview rows
4. **Convenience functions** — `sendTest()`, `sendBatch1()`, `sendBatch2()` for common batch sizes

## Email tracking

Every email sent includes an invisible tracking pixel. When a recipient opens the email, an event is recorded in Azure Cosmos DB.

From the **#SpeakingMyName** menu in your Google Sheet:

| Menu item | Does |
|---|---|
| **📬 Send Batch** | Sends emails (tracking pixel auto-embedded in HTML) |
| **🔄 Sync Tracking** | Pulls open/click counts from Azure and writes them to **Opens**, **Clicks**, **Last Open**, **Last Click** columns in the sheet |
| **📊 Sync Dashboard** | Creates/populates a "Tracking Dashboard" tab with aggregate stats + recent activity |

The sync functions call Azure, which queries Cosmos DB and returns per-email stats. See `email_tracking/README.md` for full setup.

## Requirements

- Python 3.x with `gspread`, `requests`, `python-dotenv`, `beautifulsoup4`, `openpyxl`
- Google Cloud service account with Sheets API enabled
- `.env` file in `speakhire-outreach/speakhire-outreach-simple/` with:
  - `GOOGLE_SHEET_URL`
  - `GOOGLE_APPLICATION_CREDENTIALS`
  - `DEEPSEEK_API_KEY` or `OPENROUTER_API_KEY`
- `_data/#SpeakingMyName Outreach Tracker(1).xlsx` — the manual tracker
- For sending: the Google Sheet + `smn_send.js` pasted into Apps Script
