# SpeakHire Soirée 2026 Outreach

Generates and sends personalized invitation/sponsorship emails for the **SpeakHire Annual Soirée** (June 24, 2026 at Salesforce Tower NYC).

## Files

| File | Purpose |
|---|---|
| `generate_soiree_emails.py` | **Generate** — reads a CSV of contacts, researches orgs, calls the LLM to write personalized emails, writes them to the Google Sheet |
| `soiree_send.js` | **Send** — Google Apps Script that sends approved emails from the sheet via Gmail |
| `soiree_prompt.py` | Prompt module — contains the LLM system prompts for both sponsor and individual invite emails, plus all soiree event details |

## Campaign types

The script handles **two** campaign types in one run:

| Type | Who | What |
|---|---|---|
| `sponsor` | Companies/orgs | Ask them to sponsor the Soirée (tiers: $5K–$50K+) |
| `individual` | People | Invite them to attend ($150/ticket) |

## How to use

### Step 1 — Prepare your CSV

Create a CSV with your contacts. The script auto-detects these columns (case-insensitive):

**For sponsors:**
```
name, title, organization, email, notes
```

**For individuals (richer profiles get more personalization):**
```
name, title, organization, email, languages, career interests, career field, notes
```

### Step 2 — Generate emails

```bash
# Preview contacts without calling the LLM
python generate_soiree_emails.py --csv sponsors.csv --type sponsor --preview

# Generate all sponsor emails
python generate_soiree_emails.py --csv sponsors.csv --type sponsor

# Generate only first 5 rows (test)
python generate_soiree_emails.py --csv sponsors.csv --type sponsor --rows 5

# Generate individual invites
python generate_soiree_emails.py --csv attendees.csv --type individual
```

This writes each contact to the **"Soiree Outreach"** tab in your Google Sheet with `Status = DRAFTED`.

### Step 3 — Review (optional)

Open the Google Sheet, review the drafts in columns H (Subject) and I (Body). Edit if needed — the send script sends all `DRAFTED` rows directly.

### Step 4 — Send emails

1. Open your Google Sheet
2. Go to **Extensions > Apps Script**
3. Paste the contents of `soiree_send.js`
4. Run `onOpen()` once to create the menu, or run directly:
   - `sendTest()` — sends 1 test email (row 2 only)
   - `sendBatch1()` — sends rows 2–51
   - `sendBatch2()` — sends rows 52–101
   - `previewBatch()` — dry run, shows what WOULD send

The script sends all `DRAFTED` rows (no manual approval needed). After sending, it sets `Status = SENT` with a timestamp.

### Google Sheet tab: "Soiree Outreach"

| Col | Field | Description |
|---|---|---|
| A | Contact Name | Person's name |
| B | Title | Job title |
| C | Organization | Company/org name |
| D | Email | Contact email |
| E | Campaign Type | `sponsor` or `individual` |
| F | Status | `DRAFTED` → `SENT` |
| G | Notes | Internal notes |
| H | Email Subject | AI-generated subject |
| I | Personalized Email | AI-generated body |
| J | Research Notes | Website research findings |
| K | Sent At | Timestamp when sent |
| L | Extra | Languages, interests, etc. (individual only) |

## How the code works step-by-step

### `generate_soiree_emails.py`

1. **Parse CLI args** — reads `--csv`, `--type` (sponsor/individual), `--rows`, `--preview`
2. **`read_csv()`** — auto-detects columns from your CSV (name, email, org, title, languages, interests, etc.), returns a list of contact dicts
3. **`get_sheet()`** — connects to Google Sheets via `gspread`, gets or creates the "Soiree Outreach" tab with proper headers
4. **For each contact:**
   - **`research_org()`** — if sponsor type: finds the org's website via email domain extraction + domain pattern guessing, scrapes the homepage, extracts mission/program sentences using keyword matching
   - **`build_user_prompt()`** — builds a detailed LLM prompt with org research, contact profile, and Soirée event details
   - **`call_llm()`** — sends the prompt to DeepSeek or OpenRouter (Gemma free model), parses the JSON response, handles rate limits with exponential backoff
   - **Writes to sheet** — puts subject, body, research notes, and status into the Google Sheet row
5. **Summary** — prints count of generated emails and a link to the sheet

### `soiree_prompt.py`

- Contains the **SPONSOR_SYSTEM_PROMPT** — detailed instructions for the LLM on how to write sponsorship emails (personalization rules, tone, sponsorship tiers, sender info, banned phrases)
- Contains the **INDIVIDUAL_SYSTEM_PROMPT** — instructions for personal invite emails (profile-based personalization, warm tone, ticket info)
- Contains all **Soirée facts** — date, time, venue, ticket URL, sponsorship tiers, highlights
- **`get_prompt(type)`** — returns the correct system prompt for sponsor vs individual

### `soiree_send.js`

1. **`onOpen()`** — creates the "Soiree Outreach" menu in the Google Sheets UI when the sheet opens
2. **`sendBatch()`**:
   - Reads the "Soiree Outreach" tab from `BATCH_START` to `BATCH_START + BATCH_SIZE`
   - For each row, checks: `Status === "SENT"` (skip), `Status !== "DRAFTED"` (skip), no email (error), no body (error)
   - Sends via `GmailApp.sendEmail()` with `name: "Hana from SpeakHire"`
   - Marks row as `SENT` with timestamp, sleeps 1 second between sends (Gmail rate limit)
3. **`previewBatch()`** — same logic but doesn't send; shows a summary of what would be sent
4. **Convenience functions** — `sendTest()`, `sendBatch1()`, `sendBatch2()` preset batch ranges

## Email tracking

Every email sent includes an invisible tracking pixel. When a recipient opens the email, an event is recorded in Azure Cosmos DB.

From the **Soiree Outreach** menu in your Google Sheet:

| Menu item | Does |
|---|---|
| **📬 Send Batch** | Sends emails (tracking pixel auto-embedded in HTML) |
| **🔄 Sync Tracking** | Pulls open/click counts from Azure and writes them to **Opens**, **Clicks**, **Last Open**, **Last Click** columns in the sheet |
| **📊 Sync Dashboard** | Creates/populates a "Tracking Dashboard" tab with aggregate stats + recent activity |

See `email_tracking/README.md` for full setup.

## Requirements

- Python 3.x with `gspread`, `requests`, `python-dotenv`, `beautifulsoup4`, `openpyxl`
- Google Cloud service account with Sheets API enabled (credentials JSON)
- `.env` file in `speakhire-outreach/speakhire-outreach-simple/` with:
  - `GOOGLE_SHEET_URL` — your Google Sheet URL
  - `GOOGLE_APPLICATION_CREDENTIALS` — path to service account JSON
  - `DEEPSEEK_API_KEY` or `OPENROUTER_API_KEY` — LLM API key
- For sending: the Google Sheet + `soiree_send.js` pasted into Apps Script
