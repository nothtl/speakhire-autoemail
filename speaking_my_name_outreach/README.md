# #SpeakingMyName Outreach

Generates and sends personalized partnership emails for the #SpeakingMyName campaign - people share their name story in a short video. Campaign goes LIVE on June 16th.

## Files

| File | Purpose |
|---|---|
| `generate_smn_emails.py` | Generate - reads xlsx tracker, researches org websites (parallel), calls AI, writes to sheet |
| `smn_prompt.py` | AI instructions - tone, personalization rules, sender identity (Hana Figueroa) |
| `smn_send.js` | Send - paste into Apps Script, sends DRAFTED rows via Gmail with Hana's video |

## How to use

### 1. Generate emails

```bash
# Preview (no AI calls)
python generate_smn_emails.py --preview

# Test with 3 rows
python generate_smn_emails.py --rows 3

# Research only (no AI generation)
python generate_smn_emails.py --research-only

# Generate all
python generate_smn_emails.py
```

Writes to the **"#SpeakingMyName Outreach"** tab in your Google Sheet with `Status = DRAFTED`.

### 2. Send emails

1. Open your Google Sheet → Extensions → Apps Script
2. Paste `smn_send.js`
3. Run `sendTest()` → sends 1 test email
4. Run `sendBatch1()` → sends first 50

## Sheet tab: "#SpeakingMyName Outreach"

| Col | Field | Description |
|---|---|---|
| A | Full Name | Contact person |
| B | Title | Job title |
| C | Association Name | Organization name |
| D | Type | Organization type |
| E | Contact Email | Email address |
| F | Status | `READY` → `DRAFTED` → `SENT` |
| G | Notes | Internal notes |
| H | Email Subject | AI-generated subject |
| I | Personalized Email | AI-generated body |
| J | Follow-up? | Yes/No |
| K | Sent At | Timestamp when sent |
| L | Research Notes | Website research findings |
