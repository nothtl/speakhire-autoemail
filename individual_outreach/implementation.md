# Summit Outreach - Line-by-Line Code Walkthrough

Full architecture: `../IMPLEMENTATION.md`. Shared modules: `../shared/implementation.md`.

## File map

```
individual_outreach/
├── generate_summit_emails.py   ← English generation + translation
├── summit_prompt.py            ← Alicia's voice, Active Relating Rule, speaker bios
├── summit_send.js              ← Gmail sender (Apps Script) with flyer image
└── _data/                      ← Test data + research
```

---

## `generate_summit_emails.py` - Line by Line

### Translation prompt (lines 37-57)

```python
TRANSLATION_SYSTEM_PROMPT = """You are a professional translator. Translate the following email from English into {language}.

CRITICAL RULES:
1. Translate naturally and conversationally, like a native speaker wrote it. NOT word-for-word.
2. DO NOT translate these - keep them EXACTLY as-is:
   - Person names, Company names, Organization names, Event names, URLs, Job titles
```

**Defined in the generate script, not the prompt file,** because translation is a utility function, not campaign-specific AI behavior. The `{language}` placeholder gets `.replace()`'d at runtime.

**Why the "DO NOT translate" list is so explicit:** Early versions translated "SpeakHire Summit" to "Cumbre SpeakHire" (Spanish) and "Alicia Zhuang" got transliterated. The explicit blacklist prevents this.

### `generate_email()` (lines 63-84)

```python
subject_lang = lang if lang and lang.lower() not in ('english', 'nan', '') else 'English'
```

**Subject language detection:** If the contact's Language field is "Mandarin", the subject line is written in Mandarin. If it's "English", blank, or "nan" (sheet NaN), the subject is in English. The `lang.lower()` normalization handles case variations.

```python
user_prompt = f"""
Name: {name}
Languages spoken: {lang}
Career interests: {interests if interests else 'not specified'}
Ideal future job: {job if job else 'not specified'}
CRITICAL: Write the email_subject in {subject_lang}. The email body should be in English.
{SUMMIT_CONTEXT}
```

**Subject and body can be different languages.** The subject is in the recipient's language (catches their attention), but the body is always in English (the sender writes in English). The `SUMMIT_CONTEXT` injects all speaker bios, employer lists, and run-of-show details into every prompt.

### `translate_email()` (lines 87-117)

```python
try:
    result = call_llm(system, user)
    translated = result.get('translated_email', email_body)
except Exception:
    # Fallback: raw LLM call without JSON structure
    raw_system = f'Translate the following email into {language}. Return ONLY the translated text, no JSON, no markdown.'
    resp = requests.post(...)
    translated = resp.json()['choices'][0]['message']['content'].strip()
```

**Two-tier translation fallback:**
1. **JSON-structured call:** Uses the standard `call_llm()` which expects `{"translated_email": "..."}` JSON. Clean and validated.
2. **Raw text fallback:** If JSON parsing fails (model sometimes ignores the JSON instruction when translating), a second call asks for plain text only. No JSON parsing attempted. The raw response becomes the translation.

This is the only place in the codebase where `call_llm()` is wrapped in a fallback that does a direct HTTP call. Translation is uniquely prone to JSON failures because some models are worse at JSON in non-English languages.

### Main loop (lines 143-206)

```python
needs_gen = args.force or not existing_en
needs_tr = lang and lang not in ('english', 'nan', '') and not existing_tr
```

**Two independent flags:** Generation and translation are separate decisions. A row might need translation even if the English email already exists (e.g., a previous run generated English but timed out before translating).

```python
if needs_gen and not getattr(args, 'translate_only', False):
    email_body, subject = generate_email(c)
    ws.update(values=[[subject, email_body]], range_name=f'G{sheet_row}:H{sheet_row}')
    generated += 1
    existing_en = email_body
```

**`--translate-only` flag:** Skips English generation entirely. Useful when English emails are already approved but translations need to be added or re-done.

```python
if args.rows and generated >= args.rows:
    break
```

**`--rows` counts generated emails, not processed rows.** If the first 50 rows already have English emails, `--rows 3` will scan past them (skipping) until it finds 3 that need generation, or hits the end. This is why `--rows 1` sometimes shows "Generated: 0" - the first row already had an email.

```python
# Combine
if existing_en:
    tr = existing_tr.strip() if existing_tr else ''
    if tr:
        combined = tr + '\n\n---\n\n' + existing_en.strip()
    else:
        combined = existing_en.strip()
    ws.update(values=[[combined]], range_name=f'J{sheet_row}')
```

**Combined column format:** Translation first, then `---` separator, then English. This column is what the sender (Alicia) actually copies or what the send script uses. If there's no translation, it's just the English version.

---

## `summit_prompt.py` - AI Instructions

### CAMPAIGN dict
```python
CAMPAIGN = {
    "name": "SpeakHire Summit",
    "sheet_tab": "Summit Outreach 2026",
}
```

### SUMMIT_SYSTEM_PROMPT - The Active Relating Rule

```python
HOW TO PERSONALISE - THE ACTIVE RELATING RULE:
Every speaker, program, or Summit feature you mention MUST be followed by
an explicit connection back to THIS person. Never assume the reader will
make the connection themselves. State it.

PATTERN: "[Summit thing] → [why this matters for YOU specifically]"

WEAK: "Christina Broomes, a Biopharma Executive, will be on the panel."
STRONG: "Christina Broomes, our Biopharma Executive panelist, built her career
at the intersection of science and business - the same space your STEM and
health interests point toward. She can show you what that path actually looks like."
```

**This is the most distinctive prompt engineering in the codebase.** Most AI prompts say "be personal" and leave it vague. The Active Relating Rule gives the AI a mechanical formula: name a Summit feature, then explicitly state why THIS person should care. The weak/strong example pairs show the AI exactly what to avoid and what to emulate.

### Four speaker bios (lines ~25-42)
```python
- Michael Mallon, Deputy Borough President of Queens - rose from Comms Director
  to Chief of Staff to Deputy BP. For govt, law, public service, advocacy.
- Christina Broomes, Biopharma Executive - built a career bridging science and business.
  For health, medicine, biotech, STEM.
- Frank Guia, Design Lead at Accenture - turned creative talent into a consultancy role.
  For arts, design, tech, media.
- Vicki Teman, Brand Developer - built brands and marketing strategies from nothing.
  For business, marketing, entrepreneurship.
```

Each speaker has a "For X, Y, Z" tag that tells the AI which career interests to match. The AI picks 1-2 speakers that align with the recipient's interests and actively relates them.

### Banned openings (lines ~65-72)
```python
BANNED OPENINGS (these sound fake-casual):
- "I thought of you right away..."
- "I thought of you immediately..."
- "I've been thinking about you..."

INSTEAD: Open professionally. "Hope you're doing well! This is Alicia from SpeakHire.
I noticed you're interested in health and STEM, and wanted to share something I
think you'll find valuable..."
```

**Anti-fake-casual rule.** The AI tends to over-correct for "warm tone" by pretending to be best friends with the recipient. This section explicitly bans those patterns and gives a professional but warm alternative.

### Multilingual subject line rules (lines ~80-95)
```python
The subject must be in the person's OWN language. If they speak Spanish, write it
in Spanish. If Mandarin, in Mandarin. Only use English if they speak English.

Strong examples:
- English: "Ephraim, the SpeakHire Summit this Thursday covers STEM, arts, and business"
- Spanish: "Jaime, el SpeakHire Summit este jueves conecta tus intereses en arte"
- French: "Ismatu, le SpeakHire Summit ce jeudi - carrieres en STEM et sante"
- Mandarin: "Tingli，本周四的SpeakHire Summit涵盖STEM领域"
```

The subject must include: first name, "SpeakHire Summit" (kept in English - it's the event brand), a hook from their profile, and "Thursday" or "June 11". This structured format means every subject line follows the same template but in different languages.

### SUMMIT_CONTEXT - Structured event facts

```python
RUN OF SHOW:
- 4:00-5:00: Welcome & Exploration - career fair, college pathways, museum exhibits
- 5:00-6:00: Heroes Leadership Panel - ALL FOUR speakers
- 6:00-7:00: Celebration & Community Connections - dinner, music, dance
```

Separated from the system prompt because it's injected into the user prompt (not the system prompt). The system prompt defines HOW to write; the context provides WHAT to write about. This separation means the system prompt stays clean and reusable while the context can change per event.

```python
EMPLOYERS & INDUSTRIES:
- TECH: Google, Microsoft, Netflix, IBM, Airbnb, Spotify, LinkedIn, Amazon
- GOVERNMENT: FBI (actively recruits multilingual candidates), NYC Government
```

The employer list has parenthetical notes like "(actively recruits multilingual candidates)" - these are hints to the AI to mention specific opportunities when the recipient speaks that language.
