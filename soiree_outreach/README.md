# SpeakHire Soirée 2026

Generates and sends personalized sponsorship asks and event invitations for the annual fundraising gala (June 24 at Salesforce Tower NYC).

## Files

| File | Purpose |
|---|---|
| `generate_soiree_emails.py` | Generate - reads CSV, researches companies, calls AI, writes to sheet |
| `generate_soiree_people.py` | Generate - reads Hetal's Network of Influence xlsx, writes personal invites |
| `soiree_prompt.py` | AI instructions - tone, personalization rules, event facts, sender identity |
| `soiree_send.js` | Send - paste into Apps Script, sends DRAFTED rows via Gmail |

## Campaign types

| Type | Who | Sender |
|---|---|---|
| `sponsor` | Companies/orgs | Hana, Partnerships Lead |
| `individual` | People | Hana, Community Engagement |
| `hetal_people` | Hetal's network | Hetal Jani, Founder |

## How to use

### 1. Generate emails

```bash
# Preview (no AI calls)
python generate_soiree_emails.py --csv sponsors.csv --type sponsor --preview

# Test with 3 rows
python generate_soiree_emails.py --csv sponsors.csv --type sponsor --rows 3

# Generate all
python generate_soiree_emails.py --csv sponsors.csv --type sponsor

# Individual invites
python generate_soiree_emails.py --csv attendees.csv --type individual --rows 3
```

Writes to the **"Soiree Outreach"** tab in your Google Sheet with `Status = DRAFTED`.

### 2. Send emails

1. Open your Google Sheet → Extensions → Apps Script
2. Paste `soiree_send.js`
3. Run `sendTest()` → sends 1 test email
4. Run `sendBatch1()` → sends first 50

## Sheet tab: "Soiree Outreach"

| Col | Field | Description |
|---|---|---|
| A | Contact Name | Person's name |
| B | Title | Job title |
| C | Organization | Company/org name |
| D | Email | Contact email |
| E | Campaign Type | `sponsor`, `individual`, or `hetal_people` |
| F | Status | `DRAFTED` → `SENT` |
| G | Notes | Internal notes |
| H | Email Subject | AI-generated subject |
| I | Personalized Email | AI-generated body |
| J | Research Notes | Website research findings |
| K | Sent At | Timestamp when sent |
| L | Extra | Languages, interests, donor info |
