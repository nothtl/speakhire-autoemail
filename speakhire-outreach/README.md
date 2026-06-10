# SpeakHire General Outreach

Generates and sends personalized sponsorship/partnership emails for **SpeakHire's general outreach** (sponsor, partner, and individual campaigns). This is the main workhorse — it handles the full pipeline from research to sending.

## Files

| File | Purpose |
|---|---|
| `generate.py` | **Generate** — reads the Google Sheet, researches orgs, generates personalized email drafts via LLM, writes them back |
| `outreach_send.js` | **Send** — Google Apps Script that sends approved emails from the "Outreach Tracker" tab via Gmail |
| `speakhire-outreach-simple/` | **Shared dependency** — `.env` config, Google service account credentials, and `outreach_worker.py` (the core research + LLM + sheet engine used by ALL campaigns) |
| `_data/campaign_prompts.py` | Prompt module — contains campaign-specific LLM prompts and sender configs for sponsor/partner/individual |

## Supported campaign types

| Type | Purpose |
|---|---|
| `sponsor` | Ask companies to sponsor SpeakHire financially |
| `partner` | Ask orgs to become community/program partners |
| `individual` | Invite individuals (no web research needed, uses profile notes) |

## How to use

### Step 1 — Populate the sheet

The "Outreach Tracker" tab in your Google Sheet needs these columns filled:

| Column | Field | Example |
|---|---|---|
| A | ORG_NAME | "Google" |
| B | ORG_WEBSITE | "https://about.google" |
| C | RECIPIENT | "Jane Smith" |
| D | EMAIL | "jane@google.com" |
| E | STATUS | `READY_FOR_RESEARCH` |
| F | NOTES | Any internal context |
| ... | CAMPAIGN_TYPE | `sponsor`, `partner`, or `individual` |

### Step 2 — Generate drafts

```bash
python generate.py
```

This is the **only command you need**. It:
1. Reads all rows where `STATUS = READY_FOR_RESEARCH`
2. For each row, crawls the org's website (homepage + up to 5 subpages)
3. Optionally runs a web search if a search API key is configured
4. Calls the LLM to write a personalized draft
5. Writes the draft back to the sheet with `STATUS = DRAFTED`

### Step 3 — Review & approve (optional)

Review drafts in the sheet. Edit the body directly in column I (`EMAIL_DRAFT`) or column J (`HUMAN_EDITED_DRAFT`). The send script sends all `DRAFTED` rows directly — no separate approval step.

### Step 4 — Send emails

1. Open your Google Sheet
2. Go to **Extensions > Apps Script**
3. Paste the contents of `outreach_send.js`
4. Run `onOpen()` once to create the "SpeakHire Outreach" menu, or run directly:
   - `sendTest()` — 1 test email (row 2)
   - `sendBatch1()` — rows 2–51
   - `sendBatch2()` — rows 52–101
   - `sendBatch3()` — rows 102–151
   - `previewBatch()` — dry run

The script sends all `DRAFTED` rows (no manual approval) and skips `OPT_OUT = TRUE`. It uses the human-edited draft if available, otherwise falls back to the AI draft. After sending, sets `STATUS = SENT` with a timestamp.

### Google Sheet tab: "Outreach Tracker" (37 columns)

| Key Columns | Field | Description |
|---|---|---|
| A | ORG_NAME | Organization name |
| B | ORG_WEBSITE | URL for website crawling |
| C | RECIPIENT | Contact person's name |
| D | EMAIL | Contact email |
| E | STATUS | `READY_FOR_RESEARCH` → `DRAFTED` → `SENT` |
| F | NOTES | Internal notes |
| G | PERSONALISED_OPENER | AI-generated opening paragraph |
| H | EMAIL_SUBJECT | AI-generated subject line |
| I | EMAIL_DRAFT | AI-generated full email body |
| J | HUMAN_EDITED_DRAFT | Manual edits (takes priority when sending) |
| K | SENDER_NAME | Who the email is from |
| L | OPT_OUT | Set to `TRUE` to skip this row |
| M | SENT_AT | Timestamp when sent |
| N | ERROR | Error messages |
| ... | EVIDENCE_TITLE | Specific program/initiative found in research |
| ... | SOURCE_URL | URL of the evidence |
| ... | EVIDENCE_CONFIDENCE | `HIGH` / `MEDIUM` / `LOW` |

## How the code works step-by-step

### `generate.py`

1. **Load config** — reads `.env` for `GOOGLE_SHEET_URL`, credentials, and LLM keys. Sets up `sys.path` to find `campaign_prompts.py` (in `_data/`) and `outreach_worker.py` (in `speakhire-outreach-simple/`)
2. **`read_rows()`** — connects to Google Sheets via `gspread`, reads all rows from the "Outreach Tracker" tab, normalizes headers to the 37-column schema
3. **For each row where `STATUS = READY_FOR_RESEARCH`:**
   - Validates `CAMPAIGN_TYPE` (must be sponsor/partner/individual)
   - **Patches the worker module** with campaign-specific prompt and sender from `campaign_prompts.py`
   - **`worker.research()`** — crawls the org's website (homepage + up to 5 relevant subpages). If a search API is configured (Serper/Tavily/SerpAPI), supplements with web search results
   - **`worker.generate_draft()`** — builds a detailed prompt with website text + search results, calls the LLM (DeepSeek/OpenAI-compatible), parses the JSON response with Pydantic validation. Falls back to a generic draft if LLM fails
   - **Writes back** — updates the sheet row with `STATUS = DRAFTED`, plus evidence title, summary, source URL, confidence level, personalized opener, subject, and full email body
4. **Summary** — prints count of generated drafts and any errors

### `_data/campaign_prompts.py`

- Defines LLM system prompts for each campaign type (sponsor, partner, individual)
- Defines sender profiles (name, title, org) for each type
- **`get_prompt(campaign_type)`** — returns the correct system prompt
- **`get_sender(campaign_type)`** — returns `{name, title, org}` for that campaign

### `speakhire-outreach-simple/outreach_worker.py`

This is the heavy engine shared across all campaigns. Key functions:

- **`fetch_url_text(url)`** — scrapes a single webpage, returns title + cleaned visible text (8,000 chars max)
- **`crawl_org_website(website)`** — fetches homepage, discovers internal links, scores them for relevance (about, programs, mission pages score higher), fetches up to 5 best subpages, returns combined text (12,000 chars max)
- **`research(org_name, org_website)`** — primary: website crawl (free). Secondary: search API (Serper/Tavily/SerpAPI) if configured. Returns query string, search results, and combined website text
- **`generate_draft(...)`** — tries LangChain structured output first, falls back to direct HTTP + JSON parsing, then to a hardcoded fallback template. Validates evidence confidence
- **`validate_evidence(draft, ...)`** — if confidence is HIGH, verifies the source URL actually appears in the research. Downgrades to MEDIUM if evidence is missing
- **Google Sheets I/O** — `_gsheet_read()`, `_gsheet_update_rows()` handle the full 37-column schema with header normalization
- **`send_approved()`** — Python-based email sending (alternative to the JS script) via SendGrid or Gmail SMTP. Honors `DRY_RUN` mode
- **FastAPI server** — can run as a web service (`uvicorn outreach_worker:app`) with endpoints for generate, approve, and send

### `outreach_send.js`

1. **`onOpen()`** — creates the "SpeakHire Outreach" menu
2. **`sendBatch()`**:
   - Reads the "Outreach Tracker" tab
   - For each row: skips if `SENT` or not `DRAFTED` or `OPT_OUT`
   - **`getEmailBody()`** — uses `HUMAN_EDITED_DRAFT` if available, otherwise `EMAIL_DRAFT`
   - **`getSenderName()`** — uses `SEND_FROM` > `SENDER_NAME` > default
   - **`buildEmailBody()`** — checks if the draft already has a sign-off ("Best," / "Sincerely,"). If not, appends the SpeakHire signature
   - Sends via `GmailApp.sendEmail()` with proper display name
   - Marks row as `SENT` with timestamp, sets `EMAIL_PROVIDER_STATUS = gmail_ok`
   - Sleeps 1 second between sends for Gmail rate limiting
3. **`previewBatch()`** — dry run with the same logic

## Email tracking

Every email sent includes an invisible tracking pixel. When a recipient opens the email, an event is recorded in Azure Cosmos DB.

From the **SpeakHire Outreach** menu in your Google Sheet:

| Menu item | Does |
|---|---|
| **📬 Send Batch** | Sends emails (tracking pixel auto-embedded in HTML) |
| **🔄 Sync Tracking** | Pulls open/click counts from Azure and writes them to **Opens**, **Clicks**, **Last Open**, **Last Click** columns in the sheet |
| **📊 Sync Dashboard** | Creates/populates a "Tracking Dashboard" tab with aggregate stats + recent activity |

See `email_tracking/README.md` for full setup.

## Requirements

- Python 3.x with `gspread`, `pandas`, `requests`, `python-dotenv`, `beautifulsoup4`, `pydantic`, `langchain-openai` (optional, for structured output)
- Google Cloud service account with Sheets API enabled
- `.env` file in `speakhire-outreach-simple/` with:
  - `GOOGLE_SHEET_URL`
  - `GOOGLE_APPLICATION_CREDENTIALS`
  - `DEEPSEEK_API_KEY` or `OPENAI_API_KEY` or `OPENROUTER_API_KEY`
- Optional: `SERPER_API_KEY`, `TAVILY_API_KEY`, or `SERPAPI_API_KEY` for web search
- For sending: the Google Sheet + `outreach_send.js` pasted into Apps Script
