# SpeakHire General Outreach

The original outreach engine. Handles three campaign types - sponsor, partner, and individual - with a full pipeline from research to sending.

## Files

| File | Purpose |
|---|---|
| `generate.py` | Generate - reads sheet, researches orgs, calls AI via worker engine, writes drafts |
| `outreach_prompt.py` | AI instructions - three system prompts with personalization rules, sender info |
| `outreach_worker.py` | Engine - LLM calling, website scraping, sheet I/O, email sending, FastAPI server |
| `outreach_send.js` | Send - Google Apps Script that sends DRAFTED rows via Gmail |
| `_data/prepare_leads.py` | Prepare - classifies contacts from CSV into campaign types, writes to sheet |

## Campaign types

| Type | What | Sender |
|---|---|---|
| `sponsor` | Ask companies to sponsor the Soirée | Hana, Partnerships Lead |
| `partner` | Ask orgs to become #SpeakingMyName partners | Hana, Campaign Coordinator |
| `individual` | Invite people to attend the Soirée | Hana, Community Engagement |

## How to use

### 1. Prepare leads
```bash
cd _data
python prepare_leads.py
```
Classifies contacts from `champions.csv` and `interns.csv` into the right campaign types.

### 2. Generate emails
```bash
python generate.py
```
Reads the sheet, researches orgs, calls the AI, writes drafts. Processes all rows with `STATUS=READY_FOR_RESEARCH`.

### 3. Send emails
1. Open your Google Sheet → Extensions → Apps Script
2. Paste `outreach_send.js`
3. Run from the menu

## Sheet tab: "Outreach Tracker"

This system uses a 37-column schema. Here are the columns you'll work with:

| Col | Field | Description |
|---|---|---|
| A | ORG_NAME | Organization name |
| B | ORG_WEBSITE | Their website URL |
| C | RECIPIENT | Contact person's name |
| D | EMAIL | Contact email |
| E | CONTACT_FIRST_NAME | First name (for greeting) |
| F | CONTACT_LAST_NAME | Last name |
| G | STATUS | `READY_FOR_RESEARCH` → `DRAFTED` → `APPROVED` → `SENT` |
| H | CAMPAIGN_TYPE | `sponsor`, `partner`, or `individual` |
| I | NOTES | Internal notes |
| J | PERSONALISED_OPENER | AI-generated opening paragraph |
| K | EMAIL_SUBJECT | AI-generated subject line |
| L | EMAIL_DRAFT | AI-generated email body |
| M | SENDER_NAME | Who the email is from |
| N | SENDER_TITLE | Sender's job title |
| O | SENDER_ORG | Sender's organization |
| P | OPT_OUT | Set to TRUE to skip |
| Q | ERROR | Error messages if something fails |
| R-T | EVIDENCE_* | AI-researched facts (title, summary, source URL, date, theme, confidence) |
| X-Y | CTA_TYPE, CALL_DURATION | Call-to-action details |
| Z | LAST_UPDATED | When the row was last modified |
