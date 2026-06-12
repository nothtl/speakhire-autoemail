# Summit Outreach 2026

Generates and sends personalized invitation emails for the SpeakHire Annual Summit (June 11 at Queens Museum). Each email matches the recipient's career interests to specific Summit speakers and programs. Translated for non-English speakers.

## Files

| File | Purpose |
|---|---|
| `generate_summit_emails.py` | Generate - reads sheet contacts, calls AI, translates, writes to sheet |
| `summit_prompt.py` | AI instructions - tone, speaker bios, Active Relating Rule (Alicia Zhuang) |
| `summit_send.js` | Send - paste into Apps Script, sends DRAFTED rows via Gmail with flyer image |

## How to use

### 1. Generate emails

```bash
# Test with 3 rows
python generate_summit_emails.py --rows 3

# Translate only (skip English generation)
python generate_summit_emails.py --translate-only

# Generate all
python generate_summit_emails.py
```

Reads from and writes to the **"Summit Outreach 2026"** tab in your Google Sheet.

### 2. Send emails

1. Open your Google Sheet → Extensions → Apps Script
2. Paste `summit_send.js`
3. Run `sendTest()` → sends 1 test email
4. Run `sendBatch1()` → sends first 50

## Sheet tab: "Summit Outreach 2026"

| Col | Field | Description |
|---|---|---|
| A | First Name | Recipient's first name |
| B | Last Name | Recipient's last name |
| C | Email | Contact email |
| D | Career Interests | Their interests (used for personalization) |
| E | Ideal Job | Their target role |
| F | Language | Languages spoken (triggers translation) |
| G | Email Subject | AI-generated subject (in their language) |
| H | Personalized Email (English) | AI-generated English body |
| I | Translated Email | Translation if non-English |
| J | Combined Email | Translation + English combined |
| K | Status | Tracks send state |
