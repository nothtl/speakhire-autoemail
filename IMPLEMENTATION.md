# Implementation Guide - How the Code Works

This is a technical walkthrough for anyone maintaining or modifying the email system. If you just want to use it, read `README.md` instead.

## One pattern, three campaigns

All three campaigns use the same shared engine (`shared/`). The only campaign-specific code is the AI prompt, the data source, and any special research logic.

### Shared modules (`shared/`)

```
shared/
├── config.py       ← API keys, model selection, sheet URL, credentials
└── generator.py    ← call_llm(), clean_email(), safe_str(), connect_sheet(),
                      parse_args(), fix_windows_encoding(), get_sheet_id()
```

**To change the AI model or API key:** edit `shared/config.py` only. Every campaign picks it up automatically.

### The pipeline

```
[Contact List] → [Research] → [AI generates email] → [Google Sheet] → [Gmail sends]
```

### Python scripts - what's shared vs custom

Every `generate_*.py` imports its heavy lifting from `shared/`:

```python
from shared.config import BOT_HEADERS, LLM_MODEL, BASE_URL
from shared.generator import (
    fix_windows_encoding, parse_args, call_llm,
    clean_email, safe_str, connect_sheet, get_sheet_id,
)
from *_prompt import get_prompt, CAMPAIGN
```

The campaign script itself only contains:
- **DATA READER** - reading from CSV / Excel / Google Sheets
- **RESEARCH** - campaign-specific scraping or profile lookup
- **USER PROMPT BUILDER** - how to format the AI's instructions per contact
- **MAIN** - the processing loop
DATA READER   - reads CSV / Excel / Google Sheets
SHEET WRITER  - writes generated emails to Google Sheets
MAIN          - CLI flags, processing loop
```

### AI call pattern

All scripts call the AI the same way:
1. Build a system prompt (tells the AI who it is and how to write)
2. Build a user prompt (gives the AI specific info about THIS recipient)
3. POST to `/chat/completions` with temperature 0.7
4. On rate limit (429), wait 3s/6s/12s and retry
5. Parse the JSON response, clean artifacts (em dashes, smart quotes)

The AI model: OpenRouter (free Gemma) if key is set, otherwise DeepSeek (paid).

### Apps Script - shared structure

Every `*_send.js` has these sections:

```
CONFIG       - sheet tab, batch size, sender name
SIGNATURE    - embeds SpeakHire signature image as base64 data URL
TRACKING     - 1×1 pixel for opens + link wrapping for clicks
SEND         - reads DRAFTED rows, sends via GmailApp, marks SENT
PREVIEW      - dry run showing what would send
MENU         - adds dropdown to Google Sheets UI
```

### Signature image

The signature image is fetched from Google Drive, converted to a base64 data URL, and embedded directly in the HTML. No external URLs. No `cid:` attachments. The image bytes are literally in the email HTML. This means it displays reliably in all email clients.

### Tracking pixel

A 1×1 invisible image loads from an Azure Function endpoint. The URL contains encoded metadata about the recipient. When their email client loads the image, Azure records an "open" event in Cosmos DB.

## Campaign-specific details

### Soirée (`soiree_outreach/`)

**Three generation modes:**
| Script | Input | Sender | Use case |
|---|---|---|---|
| `generate_soiree_emails.py --type sponsor` | CSV | Hana (Partnerships Lead) | Ask companies to sponsor |
| `generate_soiree_emails.py --type individual` | CSV | Hana (Community Engagement) | Invite people to attend |
| `generate_soiree_people.py` | Excel (Network of Influence) | Hetal Jani (Founder) | Hetal's personal network |

**Research:** Sponsor mode scrapes the company's website homepage and extracts mission/program sentences. Individual mode uses CSV profile fields (languages, career interests).

**Prompt file:** `soiree_prompt.py` - contains all event facts (date, venue, tiers) and three system prompts. Update this file when event details change.

### #SpeakingMyName (`speaking_my_name_outreach/`)

**Input:** Excel spreadsheet (`_data/#SpeakingMyName Outreach Tracker.xlsx`)

**Research (two phases):**
1. **Phase 1 (parallel):** 8 simultaneous workers search for each org's website (email domain → pattern guessing → Bing fallback), scrape homepage + up to 10 subpages, run a DuckDuckGo search for DEI programs
2. **Phase 2 (sequential):** AI generates one email at a time using the research

**Follow-up detection:** If the spreadsheet says `Reached Out = Yes` and `Response = Pending`, the AI writes a follow-up email instead of a fresh introduction.

**Prompt:** Defined inline in `generate_smn_emails.py`. Covers personalization rules per org type (immigrant-serving, youth/education, healthcare, cultural, government).

### Summit (`individual_outreach/`)

**Input:** Google Sheet tab "Summit Outreach 2026"

**Research:** None. Uses profile data from the sheet (career interests, ideal job, languages spoken).

**Translation:** If the person speaks a language other than English, a second AI call translates the email. The subject line is written in the recipient's language. The combined email (translation + English) goes into one column.

**Prompt file:** `summit_prompt.py` - Alicia Zhuang's voice, the "Active Relating Rule" (every Summit feature must be explicitly connected to the recipient), four speaker bios with career-path matching advice.

## Adding a new campaign

1. Create a new folder: `new_campaign/`
2. Create `*_prompt.py` with:
   ```python
   CAMPAIGN = {"name": "...", "sheet_tab": "..."}
   SYSTEM_PROMPT = """..."""
   def get_prompt(): return SYSTEM_PROMPT
   ```
3. Create `generate_*.py` using this template:
   ```python
   import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
   from shared.config import *
   from shared.generator import *
   from *_prompt import get_prompt, CAMPAIGN
   fix_windows_encoding()
   
   def read_contacts(): ...       # your data reader
   def research(contact): ...     # your research logic (optional)
   def build_prompt(contact): ... # build user prompt
   
   def main():
       args = parse_args("...")
       contacts = read_contacts()
       ws = connect_sheet(CAMPAIGN['sheet_tab'])
       for contact in contacts:
           result = call_llm(get_prompt(), build_prompt(contact))
           # write to sheet
   ```
4. Copy `*_send.js` from another campaign, update `SHEET_NAME` and `DEFAULT_SENDER_NAME`
5. Create a short README

## Common maintenance tasks

### Change the AI model
In every `generate_*.py`, find the CONFIG section and update `LLM_MODEL`.

### Update event details
Change the facts in the prompt file (e.g., `soiree_prompt.py`, `summit_prompt.py`). Everything flows from there - the AI automatically uses the new dates, venues, and links.

### Update the email signature
Change `SIGNATURE_IMAGE_ID` in every `*_send.js` file. Upload the new image to Google Drive and use its file ID.

### Add a new contact field
1. Add detection in the DATA READER section
2. Include it in the AI's user prompt (MAIN section)
3. Add it to the sheet output if needed

### Debug a broken email
If emails aren't generating:
1. Run with `--rows 1` to test one contact
2. Check the terminal output for the AI's response
3. Common issues: rate limiting (script auto-retries), invalid API key, sheet permissions
