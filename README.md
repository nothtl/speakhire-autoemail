# SpeakHire Automated Outreach

Personalized email outreach for SpeakHire - a NYC nonprofit supporting immigrant and first-gen youth.

## What this does

This system writes and sends **personalized emails at scale**. Instead of copy-pasting templates, it researches each recipient and writes a custom email that references their specific work, programs, and background. Emails are generated via AI, reviewed in Google Sheets, then sent through Gmail.

## Campaigns

| Campaign | Folder | What it sends | Sender |
|---|---|---|---|
| **Soirée** | `soiree_outreach/` | Sponsorship asks + event invitations for the annual fundraising gala (June 24) | Hana / Hetal |
| **#SpeakingMyName** | `speaking_my_name_outreach/` | Partnership emails asking orgs to join the name-story campaign (June 16) | Hana Figueroa |
| **Summit** | `individual_outreach/` | Personal invitations to the career summit at Queens Museum (June 11) | Alicia Zhuang |

Each campaign has its own folder with the same structure (see below).

## How it works (in plain English)

```
You provide a list of contacts
        ↓
Python script researches each one + calls AI to write a personal email
        ↓
Emails appear in Google Sheets for you to review
        ↓
Google Apps Script sends them through your Gmail
```

Every email includes your SpeakHire email signature image, a tracking pixel (to count opens), and click tracking on links.

## Quick start

### 1. Setup (one time)

You need a `.env` file with your API keys. It already exists at:
`speakhire-outreach/speakhire-outreach-simple/.env`

### 2. Generate emails

Each campaign has a `generate_*.py` script. Here's how to test with 3 emails:

```bash
# Soirée sponsors
cd soiree_outreach
python generate_soiree_emails.py --csv sponsors.csv --type sponsor --rows 3

# Soirée individual invites
python generate_soiree_emails.py --csv attendees.csv --type individual --rows 3

# #SpeakingMyName partners
cd ../speaking_my_name_outreach
python generate_smn_emails.py --rows 3

# Summit invites
cd ../individual_outreach
python generate_summit_emails.py --rows 3
```

Generated emails appear in your Google Sheet with status `DRAFTED`.

### 3. Send emails

1. Open your Google Sheet
2. Go to **Extensions → Apps Script**
3. Paste the `*_send.js` file for that campaign
4. Run `sendTest()` to send one test email (checks your signature, tracking, and formatting)
5. If it looks good, run `sendBatch1()` to send the first batch of 50

## Standard code structure

Every campaign folder follows the same pattern. Common code lives in `shared/` so it only needs to be written once:

```
autoemail/
├── shared/
│   ├── config.py        ← API keys, model, sheet URL (change once, applies everywhere)
│   └── generator.py     ← call_llm, clean_email, safe_str, connect_sheet, parse_args
│
├── campaign_folder/
│   ├── generate_*.py    ← thin: just data reading, research, and campaign-specific logic
│   ├── *_send.js        ← Apps Script: reads sheet, sends via Gmail
│   └── *_prompt.py      ← AI instructions + event facts (the only file you edit for new events)
```

### What lives where

| File | You edit it when... |
|---|---|
| `shared/config.py` | Changing API keys, switching AI models |
| `shared/generator.py` | Fixing a bug in LLM calling or sheet writing (rare) |
| `*_prompt.py` | **Updating event dates, speakers, ticket links, sender name** |
| `generate_*.py` | Changing how contacts are read or how the AI prompt is built |
| `*_send.js` | Changing the email signature image, sender name, or batch size |

### Python script (`generate_*.py`)

All generation scripts import from `shared/` and focus only on what's unique to their campaign:

| Section | What it does |
|---|---|
| **IMPORTS** | Libraries the script needs |
| **CONFIG** | File paths, API keys, which AI model to use |
| **PROMPT** | Instructions for the AI - either defined in the file or imported from `*_prompt.py` |
| **RESEARCH** | Looks up each recipient (website scraping, profile details) to personalize the email |
| **LLM CALLER** | Sends the prompt + research to the AI, gets back a personalized email |
| **HELPERS** | Small utility functions (clean text, handle empty values) |
| **DATA READER** | Reads your contact list (CSV, Excel, or Google Sheets) |
| **SHEET WRITER** | Connects to Google Sheets and writes the generated emails |
| **MAIN** | Ties everything together - command-line flags, the processing loop |

All scripts support these commands:
```bash
python generate_*.py --rows 3     # Generate only 3 test emails
python generate_*.py --row 5      # Generate only row 5
python generate_*.py --preview    # See who would get emailed (no AI calls)
```

### Apps Script (`*_send.js`)

All send scripts have the same structure:

| Section | What it does |
|---|---|
| **CONFIG** | Which sheet tab, batch size, sender name |
| **SIGNATURE** | Embeds your SpeakHire signature image |
| **TRACKING** | Adds invisible tracking pixel + click tracking |
| **SEND** | Reads `DRAFTED` rows, sends via Gmail, marks as `SENT` |
| **PREVIEW** | Dry run - shows what WOULD send without sending |
| **MENU** | Adds a dropdown menu to your Google Sheet |

The Google Sheet menu gives you:
- 🔍 **Preview** - see what will send
- ✉️ **Send Test** - send 1 email to verify
- 📬 **Send Batch** - send 50 at a time
- 🔄 **Sync Tracking** - pull open/click data
- 📊 **Sync Dashboard** - update stats

### Prompt file (`*_prompt.py`)

Contains the AI's instructions - tone, personalization rules, sender identity, banned phrases, and event details. This is where you update dates, ticket links, and speaker info for each new event. **The actual email-writing logic lives here**, not in the generation script.

## Email signature

All outgoing emails include your SpeakHire signature image. It's embedded directly in the email (no external links that can break).

To update the signature image:
1. Upload the new image to Google Drive
2. Copy the file ID from the share link
3. Change `SIGNATURE_IMAGE_ID` at the top of each `*_send.js` file

## Tracking

Every sent email includes:
- **Open tracking** - an invisible 1×1 pixel that records when the email is opened
- **Click tracking** - links are wrapped to record which links are clicked

Open your sheet and run **Sync Tracking** from the menu to pull the latest data.

## Requirements

- Python 3 with `gspread`, `requests`, `python-dotenv`, `beautifulsoup4`, `openpyxl`
- A Google Cloud service account with Sheets API access
- A Google Sheet where emails are stored
- An API key for DeepSeek or OpenRouter (for AI email generation)
- For sending: the Google Sheet must have Apps Script with the `*_send.js` file pasted in

## Folder overview

```
autoemail/
├── README.md                         ← You are here
├── IMPLEMENTATION.md                 ← Technical walkthrough
├── shared/                           ← Common code (used by all campaigns)
│   ├── config.py                     ← API keys, model, sheet URL
│   └── generator.py                  ← LLM caller, helpers, sheet, CLI
├── soiree_outreach/                  ← Annual fundraising gala
│   ├── generate_soiree_emails.py     ← Sponsors + individual invites
│   ├── generate_soiree_people.py     ← Hetal's personal network
│   ├── soiree_prompt.py             ← AI instructions + event details
│   └── soiree_send.js               ← Gmail sender
├── speaking_my_name_outreach/        ← Name-story campaign
│   ├── generate_smn_emails.py        ← Partner outreach
│   ├── smn_prompt.py                ← AI instructions
│   ├── smn_send.js                  ← Gmail sender
│   └── _data/                       ← Contact spreadsheet
├── individual_outreach/              ← Career summit
│   ├── generate_summit_emails.py     ← Summit invitations
│   ├── summit_prompt.py            ← AI instructions + speaker bios
│   ├── summit_send.js              ← Gmail sender
│   └── _data/                       ← Test data + research
├── email_tracking/                   ← Open + click tracking (Azure)
└── speakhire-outreach/               ← General outreach (sponsor + partner + individual)
    ├── generate.py                   ← Reads sheet, calls worker engine
    ├── outreach_prompt.py           ← AI instructions for all three types
    ├── outreach_worker.py           ← Engine (research, LLM, sheet, FastAPI)
    ├── outreach_send.js             ← Gmail sender
    └── _data/                       ← Lead CSVs + prepare_leads.py
```
