# #SpeakingMyName Outreach - Implementation

Full architecture guide at `../IMPLEMENTATION.md`. This file covers SMN-specific details.

## Data flow

```
#SpeakingMyName Outreach Tracker.xlsx → generate_smn_emails.py → Google Sheet "#SpeakingMyName Outreach" → smn_send.js → Gmail
```

## Two-phase architecture

### Phase 1: Parallel research (8 workers)
`ThreadPoolExecutor` runs `research_org()` for up to 8 orgs simultaneously. Research strategy:
1. Email domain extraction (if org email isn't gmail/yahoo)
2. Domain pattern guessing (`.org`, `.com`, `nyc.org`)
3. Bing search fallback
4. Homepage scrape + up to 10 sub-page scrapes (`/about`, `/mission`, `/programs`, `/diversity`, etc.)
5. DuckDuckGo search for DEI/inclusion programs

### Phase 2: Sequential generation
One AI call at a time (rate limit protection). `generate_email_for_org()` builds a detailed user prompt with research findings, follow-up context, and internal notes.

### Follow-up detection
If the xlsx has `Reached Out = Yes` and `Response = Pending`, the AI writes a follow-up that references prior contact instead of a fresh introduction.

## Sheet sync
`import_orgs_to_sheet()` imports new orgs from xlsx to Google Sheets (deduped by org name). `get_existing_rows()` tracks which rows already have drafts.

## Maintenance

| What | Where |
|---|---|
| Change campaign date | `smn_prompt.py` - CAMPAIGN_DATE |
| Change sender name | `smn_prompt.py` - edit signature block in SYSTEM_PROMPT |
| Change Hana's video | `smn_send.js` - SMN_VIDEO_ID |
| Change AI model | `../shared/config.py` |
