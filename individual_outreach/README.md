# Summit Outreach 2026

Generates and sends personalized invitation emails for the **SpeakHire Annual Summit** (June 11, 2026 at Queens Museum). Each email is written in the recipient's own voice — matching their career interests to specific Summit speakers and programs.

The Summit serves two groups:
- **Future Pathway Builders (ages 12–19):** Career exploration, confidence building
- **Workforce Opportunity Seekers (ages 20–26):** Internships, full-time roles, employer connections

## Files

| File | Purpose |
|---|---|
| `generate_summit_emails.py` | **Generate** — reads contacts from Google Sheets, generates personalized English emails + translations via LLM |
| `apps_script_send.js` | **Send** — Google Apps Script that sends emails with an inline flyer image from Google Drive |
| `summit_prompt.py` | Prompt module — Alicia's voice, Summit event details, speaker bios, personalization rules |
| `_data/` | Supporting data — Excel tracker, research notes, test script |

## How to use

### Step 1 — Contacts in Google Sheets

Contacts live in the **"Summit Outreach 2026"** tab:

| Col | Field | Description |
|---|---|---|
| A | First Name | Recipient's first name |
| B | Last Name | Recipient's last name |
| C | Email | Email address |
| D | Career Interests | e.g. "STEM, healthcare, arts" |
| E | Ideal Job | e.g. "Software engineer at Google" |
| F | Language | Languages spoken (for translation + subject line) |
| G | Email Subject | AI-generated (written here by script) |
| H | Personalized Email (English) | AI-generated English body |
| I | Translated Email | AI-translated version (if applicable) |
| J | Combined Email | English + translation combined |
| K | Status | `Sent` or empty |

### Step 2 — Generate emails

```bash
# Generate all remaining (rows without an English email)
python generate_summit_emails.py

# Generate only 3 test rows
python generate_summit_emails.py --rows 3

# Generate only a specific row
python generate_summit_emails.py --row 5

# Only translate existing emails (skip English generation)
python generate_summit_emails.py --translate-only
```

The script:
1. Reads all contacts from "Summit Outreach 2026"
2. For each contact without an English email, calls the LLM to generate one
3. Writes the subject to column G, English body to column H
4. If they speak another language (Spanish, French, Mandarin, etc.), **translates** the email and writes to column I
5. **Combines** both into column J (translation first, then English below)

### Step 3 — Configure the send script

Before pasting `apps_script_send.js` into Apps Script, update these at the top:

```javascript
var DRIVE_FILE_ID = "YOUR_FILE_ID_HERE"; // Google Drive file ID of your flyer image
var DRAFT_SUBJECT = "SpeakHire Summit Invitation";
```

The script embeds the flyer as an inline image at the bottom of each HTML email.

### Step 4 — Send emails

1. Open your Google Sheet
2. Go to **Extensions > Apps Script**
3. Paste the contents of `apps_script_send.js`
4. Run `onOpen()` once to create the "Mail Merge" menu, or run directly:
   - `sendTest()` — sends 1 test email (row 2)
   - `sendBatch1()` — rows 2–101
   - `sendBatch2()` — rows 102–200
5. The script sends **all unsent rows** (where `Status ≠ "Sent"`), sets `Status = "Sent {timestamp}"`

## How the code works step-by-step

### `generate_summit_emails.py`

1. **Load config** — reads `.env`, sets up LLM (OpenRouter free Gemma or DeepSeek). Imports `SUMMIT_SYSTEM_PROMPT` and `SUMMIT_CONTEXT` from `summit_prompt.py`
2. **`read_contacts()`** — connects to Google Sheets via `gspread`, reads all rows from "Summit Outreach 2026"
3. **For each contact that needs generation:**
   - **`generate_email(contact)`** — builds a user prompt with the contact's name, career interests, ideal job, and languages spoken. Tells the LLM: write the subject in THEIR language, body in English. Calls `call_llm()` with the Summit system prompt
   - **`call_llm()`** — sends to DeepSeek/OpenRouter, parses JSON response (`email_subject` + `email_body`), strips em dashes and fake-casual tics
   - **Writes to sheet** — subject → column G, English body → column H
   - **`translate_email(body, language)`** — if the person speaks Spanish, French, Mandarin, etc., translates the email using a separate translation prompt. Keeps names, company names, URLs, and event names in English. Writes to column I
   - **`combined`** — concatenates translation + English into column J
4. **Rate limiting** — sleeps 0.3 seconds between LLM calls

### `summit_prompt.py`

- **`SUMMIT_SYSTEM_PROMPT`** — Alicia Zhuang's voice. Defines:
  - The **Active Relating Rule**: every Summit feature mentioned MUST include an explicit "why this matters for YOU" connection
  - **Four speakers** with bios and matching strategies (Michael Mallon for govt/law, Christina Broomes for STEM/health, Frank Guia for design/tech, Vicki Teman for business/marketing)
  - **Summit programs** to actively relate (4pm career fair, 5pm panel, 6pm dinner + dancing, Queens Museum exhibits, college pathways, Employability Profile, AI job market discussion)
  - **Multilingual subject lines** — subject in the recipient's own language, with "SpeakHire Summit" kept in English
  - **Banned openings** ("I thought of you right away...") and banned phrases
  - **Tone rules** — under 130 words, warm and professional, no em dashes, one exclamation point max
- **`SUMMIT_CONTEXT`** — structured facts about the Summit (date, venue, run of show, all 4 speaker bios, employer/industry list, multilingual opportunities)

### `apps_script_send.js`

1. **`onOpen()`** — creates the "Mail Merge" menu
2. **`sendBatch()`**:
   - Reads "Summit Outreach 2026" from `BATCH_START` to `BATCH_START + BATCH_SIZE`
   - For each row: skips if status is `"Sent"`, skips if no email or no combined content, errors if no subject
   - **`buildHtmlBody(plainText, firstName)`** — replaces `{{First Name}}` placeholders, converts plain text to HTML with `<br>` line breaks
   - **`getInlineImageHtml()`** — generates an `<img>` tag pointing to the Google Drive file via `https://drive.google.com/uc?export=view&id=...`
   - Sends via `GmailApp.sendEmail()` with both `htmlBody` (with inline image) and plain text fallback
   - Sender: `"Alicia Zhuang from SpeakHire"`
   - Marks row as `"Sent {timestamp}"`, sleeps 1 second between sends
3. **Convenience functions** — `sendTest()`, `sendBatch1()`, `sendBatch2()`

### Key difference from other campaigns

This is the only campaign that sends **HTML emails with inline images**. The `buildHtmlBody()` function converts plain text to HTML and appends the Summit flyer image at the bottom (as an inline attachment via `cid:`, so it loads reliably). The Gmail call includes both `htmlBody` (rich version) and the plain text body as fallback.

## Email tracking

Every email sent includes an invisible tracking pixel. When a recipient opens the email, an event is recorded in Azure Cosmos DB.

From the **Mail Merge** menu in your Google Sheet:

| Menu item | Does |
|---|---|
| **📬 Send Batch** | Sends emails (tracking pixel auto-embedded in HTML) |
| **🔄 Sync Tracking** | Pulls open/click counts from Azure and writes them to **Opens**, **Clicks**, **Last Open**, **Last Click** columns in the sheet |
| **📊 Sync Dashboard** | Creates/populates a "Tracking Dashboard" tab with aggregate stats + recent activity |

See `email_tracking/README.md` for full setup.

## Requirements

- Python 3.x with `gspread`, `requests`, `python-dotenv`
- Google Cloud service account with Sheets API enabled
- `.env` file in `speakhire-outreach/speakhire-outreach-simple/` with:
  - `GOOGLE_SHEET_URL`
  - `GOOGLE_APPLICATION_CREDENTIALS`
  - `DEEPSEEK_API_KEY` or `OPENROUTER_API_KEY`
- For sending: Google Sheet + `apps_script_send.js` pasted into Apps Script
- Summit flyer image hosted on Google Drive (shared as "Anyone with the link")
