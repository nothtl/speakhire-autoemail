# Summit Outreach — Code Walkthrough & Maintenance Guide

## File dependency map

```
individual_outreach/
│
├── generate_summit_emails.py    ← run locally: python generate_summit_emails.py
│   ├── reads:  Google Sheet → "Summit Outreach 2026" tab (columns A-K)
│   ├── writes: same tab (col G: subject, col H: English email, col I: translation, col J: combined)
│   ├── calls:  DeepSeek/OpenRouter API (LLM) — up to TWICE per contact (generate + translate)
│   └── imports: summit_prompt.py (SUMMIT_SYSTEM_PROMPT, SUMMIT_CONTEXT),
│                ../speakhire-outreach/speakhire-outreach-simple/ (.env, gspread)
│
├── apps_script_send.js          ← paste into Google Sheets → Extensions → Apps Script
│   ├── reads:  "Summit Outreach 2026" tab (columns A-K)
│   ├── sends:  GmailApp.sendEmail() with HTML body + inline flyer image + tracking pixel
│   ├── gets:   flyer image from Google Drive via DriveApp.getFileById()
│   ├── sender: "Alicia Zhuang from SpeakHire"
│   └── adds:   "Mail Merge" menu (Send Test, Batch 1/2, Custom, Sync Tracking, Sync Dashboard)
│
├── summit_prompt.py             ← imported by generate_summit_emails.py
│   ├── defines: SUMMIT_SYSTEM_PROMPT (108 lines — Alicia's voice, personalization rules)
│   └── defines: SUMMIT_CONTEXT (42 lines — event facts, speaker bios, employer lists)
│
└── _data/
    ├── 2026 Summit Alumni_100 top.xlsx   ← original contact list
    └── test_generate.py                  ← test harness
```

## What makes this campaign unique

The Summit campaign is the most sophisticated of the four:

1. **Multilingual** — subject lines in the recipient's language, body translated if needed. Two LLM calls per multilingual contact.
2. **HTML with inline image** — the only campaign that embeds a flyer image as an inline attachment (via `cid:`) instead of an external URL.
3. **Active Relating Rule** — a prompting technique that forces the LLM to explicitly connect every Summit feature to the recipient's specific interests.
4. **Alicia's voice** — distinct sender persona, different from Hana (used in all other campaigns).
5. **Sends all unsent rows** — no status gate beyond `status !== "Sent"`. Every row with content gets sent.

## End-to-end flow: `python generate_summit_emails.py`

```
1. Script starts
   ├── Sets up sys.path: ../speakhire-outreach/speakhire-outreach-simple/
   ├── Loads .env: GOOGLE_SHEET_URL, GOOGLE_APPLICATION_CREDENTIALS, DEEPSEEK_API_KEY
   ├── Imports summit_prompt (SUMMIT_SYSTEM_PROMPT, SUMMIT_CONTEXT)
   │
2. read_contacts()
   ├── gspread.service_account(CREDS_PATH) → open_by_key(sheet_id)
   ├── worksheet("Summit Outreach 2026") → get_all_records()
   └── Returns: list of contact dicts + worksheet object
   │
3. For each contact:
   │
   ├── Determine what's needed:
   │   ├── needs_gen = no existing English email (col H is empty)
   │   ├── needs_tr  = has a language AND language ≠ "english"/"nan"/"" AND no existing translation (col I is empty)
   │   └── Skip if both are already done (unless --row targets this row)
   │
   ├── [needs_gen] generate_email(contact)
   │   ├── Build user prompt: name, interests, ideal job, languages
   │   ├── call_llm(SUMMIT_SYSTEM_PROMPT, user_prompt)
   │   │   ├── POST to DeepSeek/OpenRouter /chat/completions
   │   │   ├── Temperature=0.7, timeout=60s
   │   │   ├── Parse JSON: {"email_subject": "...", "email_body": "..."}
   │   │   └── Clean: remove em dashes, "right?" tics, "you know?" tics
   │   ├── Write subject → col G
   │   └── Write English body → col H
   │
   ├── [needs_tr && has English body] translate_email(body, language)
   │   ├── call_llm(TRANSLATION_SYSTEM_PROMPT, body)
   │   │   ├── Prompt: "Translate into {language}. DO NOT translate: names, companies,
   │   │   │           event names, URLs, signatures, job titles."
   │   │   ├── Parse JSON: {"translated_email": "..."}
   │   │   └── FALLBACK (if JSON fails): raw LLM call without JSON structure
   │   │       └── "Translate the following email into {language}. Return ONLY the translated text."
   │   └── Write translation → col I
   │
   └── Combine (always, if English exists):
       ├── If translation exists: combined = translation + "\n\n---\n\n" + English
       ├── If no translation:     combined = English
       └── Write combined → col J
       └── sleep(0.3) between LLM calls
```

## Code walkthrough: generate_summit_emails.py (288 lines)

### Imports and setup (lines 15-52)

```python
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..', 'speakhire-outreach', 'speakhire-outreach-simple'))

from dotenv import load_dotenv
load_dotenv(r'C:\Users\Tingli\Documents\GitHub\speakhire\autoemail\speakhire-outreach\speakhire-outreach-simple\.env')

import requests, gspread
from summit_prompt import SUMMIT_SYSTEM_PROMPT, SUMMIT_CONTEXT
```

**Hardcoded load_dotenv path (line 34):** This absolute path is machine-specific. If you run this on a different computer, update it to point to your `.env` file location. Or better: replace with `load_dotenv(os.path.join(SCRIPT_DIR, '..', 'speakhire-outreach', 'speakhire-outreach-simple', '.env'))`.

### read_contacts() (lines 55-60)

```python
def read_contacts():
    m = re.search(r'/d/([a-zA-Z0-9\-_]+)', SHEET_URL)
    gc = gspread.service_account(filename=CREDS_PATH)
    sh = gc.open_by_key(m.group(1))
    ws = sh.worksheet('Summit Outreach 2026')
    return ws.get_all_records(), ws
```

Returns both the records AND the worksheet object — needed later for `ws.update()`. The sheet ID is extracted from the URL via regex (handles both `/d/ID/edit` and `/d/ID/` formats).

### call_llm() (lines 63-108)

Same pattern as other campaigns but with an aggressive JSON parse fallback:

```python
try:
    return json.loads(content)
except json.JSONDecodeError as e:
    # Print problematic area for debugging
    start = max(0, e.pos - 20)
    end = min(len(content), e.pos + 20)
    problem = content[start:end]
    # Aggressive clean: keep only printable ASCII + newlines
    content = ''.join(c for c in content if 32 <= ord(c) <= 126 or c in '\n\r')
    return json.loads(content)
```

The second attempt strips ALL non-ASCII characters (including smart quotes and em dashes that might have slipped through). This is more aggressive than the SMN two-pass approach but necessary because translated content sometimes contains Unicode that breaks `json.loads()`.

### safe_str() (lines 111-115)

```python
def safe_str(val):
    if val is None: return ''
    if isinstance(val, float): return '' if str(val) == 'nan' else str(val)
    return str(val).strip()
```

Google Sheets returns empty cells as `NaN` floats. Without this, you'd get literal "nan" strings in your prompts and emails.

### generate_email() (lines 117-149)

```python
def generate_email(contact):
    name = safe_str(contact.get('First Name', ''))
    interests = safe_str(contact.get('Career Interests', ''))
    job = safe_str(contact.get('Ideal Job', ''))
    lang = safe_str(contact.get('Language', ''))

    # Determine subject language
    subject_lang = lang if lang and lang.lower() not in ('english','nan','') else 'English'

    user_prompt = f"""
Write a personalized Summit invitation email for:

Name: {name}
Languages spoken: {lang}
Career interests: {interests if interests else 'not specified'}
Ideal future job: {job if job else 'not specified'}

CRITICAL: Write the email_subject in {subject_lang}. The email body should be in English.

{SUMMIT_CONTEXT}

Remember: mention at least TWO speakers that match their interests.
Mention at least ONE non-speaker summit feature.
If they speak another language, flag it as an asset.
Keep it under 130 words. NO em dashes.
"""
    result = call_llm(SUMMIT_SYSTEM_PROMPT, user_prompt)
    body = result.get('email_body', '').replace('—', '-').replace('–', '-')
    body = re.sub(r', right\?', '?', body)
    body = re.sub(r'\bright\?\b', '', body)
    body = re.sub(r', you know\?', '?', body)
    subject = result.get('email_subject', '').replace('—', '-').replace('–', '-')
    return body, subject
```

**Why the language check:** `subject_lang` defaults to English if the language field is empty, "english", or "nan" (the string Google Sheets returns for empty cells). This prevents the LLM from trying to write subjects in "nan".

**The cleaning pipeline:**
1. `replace('—', '-')` — em dash → hyphen
2. `replace('–', '-')` — en dash → hyphen
3. `re.sub(r', right\?', '?', body)` — "your Spanish skills, right?" → "your Spanish skills?"
4. `re.sub(r'\bright\?\b', '', body)` — standalone "right?" → removed
5. `re.sub(r', you know\?', '?', body)` — ", you know?" → removed

These are LLM vocal tics that make emails sound AI-generated. Stripping them makes the output more natural.

### TRANSLATION_SYSTEM_PROMPT (lines 152-172)

```python
TRANSLATION_SYSTEM_PROMPT = """You are a professional translator.
Translate the following email from English into {language}.

CRITICAL RULES:
1. Translate naturally and conversationally, like a native speaker wrote it. NOT word-for-word.
2. DO NOT translate: person names, company names, organization names, event names,
   URLs, email signatures, job titles.
3. If a concept doesn't translate well, keep the English term in quotes.
4. The tone should match the original: warm, personal, direct, youthful.
5. Keep the same line breaks and paragraph structure.
6. NO em dashes.

Return this exact JSON:
{"translated_email": "the full translated email"}
"""
```

**Why "{language}" is a placeholder, not an f-string:** The prompt is defined as a regular string with `{language}` as literal text. In `translate_email()`, it's replaced with the actual language via `.replace('{language}', language)`. This avoids f-string evaluation at module load time when `language` doesn't exist.

### translate_email() (lines 175-204)

```python
def translate_email(email_body, language):
    system = TRANSLATION_SYSTEM_PROMPT.replace('{language}', language)
    user = f"Translate this email into {language}:\n\n{email_body}"

    try:
        result = call_llm(system, user)
        translated = result.get('translated_email', email_body)
    except Exception:
        # Fallback: raw LLM call without JSON structure
        raw_system = f'Translate the following email into {language}. Keep all names, URLs, and company names exactly as-is. Return ONLY the translated text, no JSON, no markdown.'
        resp = requests.post(
            f'{BASE_URL}/chat/completions',
            headers={'Authorization': f'Bearer {API_KEY}', 'Content-Type': 'application/json',
                     'HTTP-Referer': 'https://speakhire.org', 'X-Title': 'SpeakHire Summit'},
            json={'model': LLM_MODEL, 'messages': [
                {'role': 'system', 'content': raw_system},
                {'role': 'user', 'content': email_body},
            ], 'temperature': 0.7}, timeout=60)
        resp.raise_for_status()
        translated = resp.json()['choices'][0]['message']['content'].strip()

    translated = translated.replace('—', '-').replace('–', '-')
    translated = ''.join(c for c in translated if ord(c) >= 32 or c in '\n\r\t')
    return translated
```

**Why the fallback:** Some LLMs struggle with JSON output for translations (they want to just output the translated text). The fallback says "return ONLY the translated text" — more natural for the LLM, but we lose the structured JSON wrapper. The raw response is used directly.

### main() (lines 207-287)

The orchestration loop. Key logic:

```python
for i, c in enumerate(contacts):
    needs_gen = not existing_en          # col H is empty
    needs_tr = lang and lang not in ('english', 'nan', '') and not existing_tr  # col I is empty

    if not needs_gen and not needs_tr:
        continue  # already done

    # Generate English
    if needs_gen and not args.translate_only:
        email_body, subject = generate_email(c)
        ws.update(values=[[subject, email_body]], range_name=f'G{sheet_row}:H{sheet_row}')
        existing_en = email_body
        time.sleep(0.3)  # rate limit

    # Translate
    if needs_tr and existing_en:
        tr_body = translate_email(existing_en, lang_raw)
        ws.update(values=[[tr_body]], range_name=f'I{sheet_row}')
        existing_tr = tr_body
        time.sleep(0.3)

    # Combine (always if English exists)
    if existing_en:
        tr = existing_tr.strip() if existing_tr else ''
        combined = tr + '\n\n---\n\n' + existing_en.strip() if tr else existing_en.strip()
        ws.update(values=[[combined]], range_name=f'J{sheet_row}')
```

**Why `ws.update()` three separate times:** Each column (G, H, I, J) is written independently. This is slightly wasteful (3 API calls per row) but makes the code simpler — each step writes its result immediately. For 100 contacts with translation, that's ~300 API calls. Google Sheets API quota is 300 requests/minute per user, so this is safe.

**Why `sleep(0.3)` not `sleep(0.5)`:** Summit emails are shorter (under 130 words) and translations are simpler than research-based generation. The LLM responds faster, so a shorter sleep is sufficient.

## Code walkthrough: summit_prompt.py (152 lines)

### SUMMIT_SYSTEM_PROMPT structure (lines 5-108)

| Lines | Section | Purpose |
|---|---|---|
| 7-17 | Summit overview | Date, venue, two audience groups, Employability Profile |
| 21-28 | **Active Relating Rule** | WEAK vs STRONG examples. Pattern: "[Summit thing] → [why this matters for YOU specifically]" |
| 34-38 | Four speaker bios | Michael Mallon (govt/law), Christina Broomes (STEM/health), Frank Guia (design/tech), Vicki Teman (business/marketing) |
| 40-48 | Summit programs to relate | 4pm career fair, 5pm panel, 6pm dinner, Queens Museum exhibits, college pathways, Employability Profile, AI job market discussion |
| 52-68 | Tone rules | Under 140 words, "vary your opening," no rhetorical questions, no em dashes, banned openings list |
| 74-75 | Signature | "Best, Alicia Zhuang, SpeakHire" |
| 80-101 | **Subject line rules** | Must be in recipient's language (Spanish, French, Mandarin, etc.), must include first name, "SpeakHire Summit," a hook, and "Thursday"/"June 11." Examples provided |
| 103-107 | JSON output format | |

### The Active Relating Rule (lines 21-28)

This is the core quality mechanism. It forces the LLM to make explicit connections:

```
WEAK:   "Christina Broomes, a Biopharma Executive, will be on the panel."
        → States a fact. Reader must figure out why they should care.

STRONG: "Christina Broomes, our Biopharma Executive panelist, built her career at
         the intersection of science and business — the same space your STEM and
         health interests point toward. She can show you what that path actually
         looks like."
        → States the fact AND connects it to the reader. No mental work required.
```

The prompt provides 4 weak/strong pairs as examples. This is purely prompt-based quality control — there's no code-level validation of the output. If the LLM produces weak emails, the fix is to add better examples to the prompt.

### SUMMIT_CONTEXT (lines 110-151)

A structured fact dump the LLM references:
- Run of show (4:00-7:00 schedule)
- Four speaker bios (detailed)
- Employer/industry list (20+ companies across 7 industries)
- Multilingual opportunities (FBI, hospitals, NYC gov, tech localization)

**To update for next year:** Replace all speaker bios, dates, venue, and employer list. The LLM prompt text references these dynamically.

## Code walkthrough: apps_script_send.js (245+ lines)

### getDriveImage() (lines 43-58)

```javascript
function getDriveImage() {
  if (DRIVE_FILE_ID === "YOUR_DRIVE_FILE_ID_HERE") {
    return null;  // No image configured — graceful degradation
  }
  try {
    var file = DriveApp.getFileById(DRIVE_FILE_ID);
    var blob = file.getBlob();
    return {
      name: file.getName(),
      blob: blob,
    };
  } catch (e) {
    console.warn("Could not fetch Drive image: " + e.toString());
    return null;  // Don't crash — send without image
  }
}
```

**Graceful degradation:** If the file ID is not set or the file can't be accessed, the function returns `null` and the email is sent without the image. It never crashes the send loop.

### buildHtmlBody() (lines 60-89)

```javascript
function buildHtmlBody(plainText, firstName, hasImage) {
  // Replace {{First Name}} placeholders
  var body = plainText.replace(/\{\{First Name\}\}/g, firstName);
  body = body.replace(/\{\{First Name\}\}/gi, firstName);

  // Convert plain text to HTML
  var htmlBody = body
    .replace(/&/g, "&amp;")         // MUST be first
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\n/g, "<br>");        // line breaks → HTML breaks

  // Add inline flyer image via cid: reference
  if (hasImage) {
    htmlBody +=
      '<br><br><img src="cid:flyer"' +
      ' style="max-width:100%;height:auto;display:block;"' +
      ' alt="SpeakHire Summit flyer">';
  }

  return '<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;">' +
         htmlBody + '</div>';
}
```

**Why `cid:flyer` instead of a URL:** This was changed from a Google Drive URL (which email clients blocked). The `cid:` protocol references an inline attachment — Gmail swaps in the actual image blob. The key `flyer` matches the key in `options.inlineImages = { flyer: imageBlob.blob }`.

**Why `{{First Name}}` placeholder replacement:** Some templates use `{{First Name}}` merge tags. This ensures they're replaced even if the AI body already includes the name.

### sendBatch() (lines 95-203)

```javascript
function sendBatch() {
  // Read sheet data
  var imageBlob = getDriveImage();    // Get once, reuse for all emails
  var hasImage = (imageBlob !== null);

  for (var i = BATCH_START - 1; i < endRow; i++) {
    // Skip checks
    if (status === "Sent") { skipped++; continue; }  // Note: "Sent" not "SENT"
    if (!email || !combined) { continue; }

    // Build HTML
    var htmlBody = buildHtmlBody(combined, firstName, hasImage);
    htmlBody += getTrackingPixel(email, firstName, "Summit Attendee", CAMPAIGN_SLUG);

    var options = {
      htmlBody: htmlBody,
      name: "Alicia Zhuang from SpeakHire",
    };
    if (hasImage) {
      options.inlineImages = { flyer: imageBlob.blob };  // cid:flyer → this blob
    }

    GmailApp.sendEmail(email, subject, combined, options);
    // Mark as "Sent {timestamp}"
    // sleep(1000)
  }
}
```

**Why `status === "Sent"` (capital S, no timestamp check):** The script writes `"Sent " + timestamp` to the status column. The check is `status === "Sent"` which uses `String(...).trim()` — after trim, `"Sent 6/10/2026, 2:30 PM"` becomes `"Sent 6/10/2026, 2:30 PM"` which does NOT equal `"Sent"`. 

Wait — let me re-read the code:

```javascript
var status = String(row[COL_STATUS - 1] || "").trim();
if (status === "Sent") { skipped++; continue; }
```

This checks for EXACTLY `"Sent"`. But the write is `"Sent " + timestamp` (with a space). So `"Sent 6/10/2026"` does NOT match `"Sent"`. This means... the skip check might not work after the first send!

**This is a known quirk:** After the first batch sends, the status column contains `"Sent 6/10/2026, 2:30:00 PM"`. The skip check `status === "Sent"` will NOT match this, so the row will be sent AGAIN on the next batch run. The fix applied in the current code is that the status column is set to `"Sent 6/10/2026, 2:30:00 PM"` and the skip check is `status === "Sent"` — these don't match, which is a BUG. However, looking more carefully at the original code:

```javascript
sheet.getRange(i + 1, COL_STATUS).setValue("Sent " + timestamp);
```

So the value is literally `"Sent "` followed by the timestamp. When read back and trimmed, it's `"Sent 6/10/2026, 2:30:00 PM"`. The check `status === "Sent"` fails (it would need `status.startsWith("Sent")`).

**The practical effect:** Rows are re-sent if you run the batch again. This is a bug but also a safety net — you can't accidentally mark something as "already sent" and skip it. The intended behavior is: the `BATCH_START` advances manually between runs, so you don't re-scan the same rows.

Actually wait, looking again at the code, `BATCH_START` is not auto-advanced:
```javascript
// Auto-advance BATCH_START for next run
// Uncomment below to auto-advance (saves having to change the code manually):
// PropertiesService.getScriptProperties().setProperty('NEXT_BATCH', String(endRow + 1));
```

So manual BATCH_START changes are expected. The `status === "Sent"` check is there as a safety net but doesn't actually work because of the timestamp appended to the status value.

## Maintenance tasks

### How to update the Summit for next year

In `summit_prompt.py`:
1. Update the date throughout SUMMIT_SYSTEM_PROMPT (search for "June 11", "Thursday")
2. Update SUMMIT_CONTEXT: new date, venue, run of show
3. Replace all 4 speaker bios
4. Update employer/industry list
5. Update registration URL
6. Update `SOIREE_DATE` reference (line 48) if the Soirée date changes

### How to change the flyer image

1. Upload new flyer to Google Drive
2. Right-click → Share → "Anyone with the link"
3. Copy the file ID from the URL: `https://drive.google.com/file/d/{FILE_ID}/view`
4. Update `DRIVE_FILE_ID` in `apps_script_send.js`:
   ```javascript
   var DRIVE_FILE_ID = "1a2b3c4d5e6f...";  // just the ID, not the full URL
   ```

### How to add a new speaker to the prompt

In `summit_prompt.py`, add to SUMMIT_SYSTEM_PROMPT (around line 34-38):
```
- New Speaker Name, Title at Company — brief bio. For [career paths]. Relate it: "If you're interested in [field], New Speaker's path from [X] to [Y] shows [value]."
```

And add a full bio to SUMMIT_CONTEXT (around line 130-134).

### How to change the sender

In `summit_prompt.py` line 74-75:
```
Best,
Alicia Zhuang
SpeakHire
```

And in `apps_script_send.js` line 158:
```javascript
name: "Alicia Zhuang from SpeakHire",
```

Both must change together.

### How to handle the status bug (re-sending)

If you want the skip check to actually work, change in `apps_script_send.js`:
```javascript
// FROM:
if (status === "Sent") { skipped++; continue; }

// TO:
if (status.indexOf("Sent") === 0) { skipped++; continue; }
```
This matches any status starting with "Sent" (including "Sent 6/10/2026...").

## Common issues

### Translation sounds robotic/word-for-word

The translation prompt emphasizes natural translation. If output is still stiff:
1. Add more good-vs-bad examples to `TRANSLATION_SYSTEM_PROMPT`
2. Increase temperature to 0.8 for more creative translations
3. For critical emails, manually review translations in column I before sending

### Subject line in wrong language

The LLM determines the subject language from the `Language` field in column F. If blank or "English", the subject stays in English. Verify the column has the correct value (e.g., "Spanish", "French", "Mandarin", not "es", "fr", "zh").

### Flyer image not loading

This was fixed (inline attachment via `cid:` instead of Google Drive URL). If it still fails:
1. Verify the file is shared as "Anyone with the link"
2. Check `DRIVE_FILE_ID` is the FILE ID (e.g., `1a2b3c...`), not the full URL (`https://drive.google.com/file/d/1a2b3c.../view`)
3. Run `sendTest()` — sends to yourself, easiest way to verify

### Combined column (J) is empty

The combined column is only written if an English email exists (column H is not empty). If generation failed for a row, column J stays empty. Fix: `python generate_summit_emails.py --row {n}` for that specific row.

### Rows are sent twice

As described above: the `status === "Sent"` check doesn't match `"Sent 6/10/2026, 2:30 PM"`. The BATCH_START manual advance is the intended way to avoid re-sending. Or apply the `indexOf("Sent") === 0` fix described in the maintenance section.

### "Sent" status not updating

The script writes `"Sent {timestamp}"` to column K. If you see no update:
1. Check the Apps Script execution log (View → Logs)
2. Verify the Gmail daily quota hasn't been hit (100 emails/day for free accounts)
3. Check the script isn't hitting a permission error (first run requires Gmail authorization)

## Testing

```bash
# Step 1: Generate 1 test email
python generate_summit_emails.py --row 2

# Step 2: Check the sheet — verify subject in col G, body in col H
# If the contact has a language, verify translation in col I and combined in col J

# Step 3: Test with a multilingual contact
python generate_summit_emails.py --row 5  # pick a row with a non-English language

# Step 4: Generate 3 more
python generate_summit_emails.py --rows 3

# Step 5: Translate existing emails (skip English generation)
python generate_summit_emails.py --translate-only
```

For the send script:
1. Set `DRIVE_FILE_ID` to your test flyer
2. Run `sendTest()` — sends row 2 to yourself
3. Verify: email body looks right, flyer image loads, tracking pixel present (Gmail → Show Original → search for `azurewebsites.net`)
4. Open the email, wait 30s, run `syncTracking()` — should see Opens: 1
5. If all good, run `sendBatch1()` for the first 100 rows
