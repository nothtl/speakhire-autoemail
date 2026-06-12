"""
generate_summit_emails.py — SpeakHire Summit 2026 Email Generator

Reads contacts from the "Summit Outreach 2026" Google Sheet tab. Generates
personalized invitations from Alicia Zhuang. Translates for non-English speakers.

Usage:
  python generate_summit_emails.py                  # generate all remaining
  python generate_summit_emails.py --rows 3         # generate only 3 rows
  python generate_summit_emails.py --row 5          # generate only row 5
  python generate_summit_emails.py --translate-only # only translate existing emails
"""

# ═══════════════════════════════════════════════════
# IMPORTS
# ═══════════════════════════════════════════════════

import os, re, sys, time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..'))

from shared.config import LLM_MODEL, BASE_URL
from shared.generator import (
    fix_windows_encoding, parse_args, call_llm,
    clean_email, safe_str, connect_sheet, get_sheet_id,
)
from summit_prompt import get_prompt, SUMMIT_CONTEXT, CAMPAIGN

fix_windows_encoding()

import gspread

# ═══════════════════════════════════════════════════
# TRANSLATION PROMPT
# ═══════════════════════════════════════════════════

TRANSLATION_SYSTEM_PROMPT = """You are a professional translator. Translate the following email from English into {language}.

CRITICAL RULES - follow these exactly:
1. Translate naturally and conversationally, like a native speaker wrote it. NOT word-for-word.
2. DO NOT translate these - keep them EXACTLY as-is:
   - Person names (the recipient's name, the sender's name "Alicia Zhuang")
   - Company names (Google, Accenture, FBI, Genentech, JP Morgan, Netflix, etc.)
   - Organization names (SpeakHire, Queens Museum, NYC Government, etc.)
   - Event names (SpeakHire Summit, Heroes Leadership Panel, Employability Profile)
   - URLs (the zeffy.com registration link)
   - Email signatures (the "Best, Alicia Zhuang SpeakHire" block)
   - Job titles like "Deputy Borough President", "Biopharma Executive", "Design Lead", "Brand Developer"
3. If a concept doesn't translate well, keep the English term in quotes rather than forcing a bad translation.
4. The tone should match the original: warm, personal, direct, youthful.
5. Keep the same line breaks and paragraph structure.
6. NO em dashes. Use commas or regular dashes instead.

Return this exact JSON:
{{
  "translated_email": "the full translated email"
}}"""


# ═══════════════════════════════════════════════════
# EMAIL GENERATION
# ═══════════════════════════════════════════════════

def generate_email(contact):
    """Generate a personalized Summit invitation email in English."""
    name = safe_str(contact.get('First Name', ''))
    interests = safe_str(contact.get('Career Interests', ''))
    job = safe_str(contact.get('Ideal Job', ''))
    lang = safe_str(contact.get('Language', ''))

    subject_lang = lang if lang and lang.lower() not in ('english', 'nan', '') else 'English'

    user_prompt = f"""
Write a personalized Summit invitation email for:

Name: {name}
Languages spoken: {lang}
Career interests: {interests if interests else 'not specified'}
Ideal future job: {job if job else 'not specified'}

CRITICAL: Write the email_subject in {subject_lang}. The email body should be in English.

{SUMMIT_CONTEXT}

Remember: mention at least TWO speakers that match their interests. Mention at least ONE non-speaker summit feature. If they speak another language, flag it as an asset. Keep it under 130 words. NO em dashes.
"""
    system_prompt = get_prompt()
    result = call_llm(system_prompt, user_prompt)
    body = clean_email(result.get('email_body', ''))
    subject = clean_email(result.get('email_subject', ''))
    print(f'  Subject: {subject}')
    return body, subject


def translate_email(email_body, language):
    """Translate an email into the target language."""
    system = TRANSLATION_SYSTEM_PROMPT.replace('{language}', language)
    user = f"Translate this email into {language}:\n\n{email_body}"

    try:
        result = call_llm(system, user)
        translated = result.get('translated_email', email_body)
    except Exception:
        # Fallback: raw LLM call without JSON structure
        raw_system = (
            f'Translate the following email into {language}. '
            'Keep all names, URLs, and company names exactly as-is. '
            'Return ONLY the translated text, no JSON, no markdown.'
        )
        import requests
        from shared.config import API_KEY, BASE_URL as _BASE_URL, LLM_MODEL as _MODEL, OPENROUTER_KEY

        headers = {
            'Authorization': f'Bearer {API_KEY}',
            'Content-Type': 'application/json',
        }
        if OPENROUTER_KEY:
            headers['HTTP-Referer'] = 'https://speakhire.org'
            headers['X-Title'] = 'SpeakHire Summit'
        resp = requests.post(
            f'{_BASE_URL}/chat/completions',
            headers=headers,
            json={
                'model': _MODEL,
                'messages': [
                    {'role': 'system', 'content': raw_system},
                    {'role': 'user', 'content': email_body},
                ],
                'temperature': 0.7,
            },
            timeout=90,
        )
        resp.raise_for_status()
        translated = resp.json()['choices'][0]['message']['content'].strip()

    translated = clean_email(translated)
    return translated


# ═══════════════════════════════════════════════════
# SHEET HELPERS
# ═══════════════════════════════════════════════════

def read_contacts(ws):
    """Read all contacts from the sheet."""
    return ws.get_all_records()


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════

def main():
    args = parse_args(
        "Generate Summit outreach emails",
        extra_args=[
            {"name": "--translate-only", "dest": "translate_only", "action": "store_true",
             "help": "Only translate existing emails, skip generation"},
        ]
    )

    ws = connect_sheet(CAMPAIGN['sheet_tab'])
    contacts = read_contacts(ws)
    print(f'Read {len(contacts)} contacts from "{CAMPAIGN["sheet_tab"]}"')
    print(f'Using: {LLM_MODEL} via {BASE_URL}')
    print()

    generated = 0
    translated = 0

    for i, c in enumerate(contacts):
        sheet_row = i + 2
        first = safe_str(c.get('First Name', '?'))
        interests = safe_str(c.get('Career Interests', ''))[:50]
        lang_raw = safe_str(c.get('Language', ''))
        lang = lang_raw.lower() if lang_raw else ''
        existing_en = safe_str(c.get('Personalized Email (English)', ''))
        existing_tr = safe_str(c.get('Translated Email', ''))

        needs_gen = args.force or not existing_en
        needs_tr = lang and lang not in ('english', 'nan', '') and not existing_tr

        if not needs_gen and not needs_tr:
            continue

        if args.row is not None and sheet_row != args.row:
            continue

        # Generate English
        if needs_gen and not getattr(args, 'translate_only', False):
            print(f'Row {sheet_row}: {first} ({lang_raw}) [{interests}]')
            try:
                email_body, subject = generate_email(c)
                ws.update(values=[[subject, email_body]], range_name=f'G{sheet_row}:H{sheet_row}')
                generated += 1
                existing_en = email_body
                time.sleep(0.3)
            except Exception as e:
                print(f'  GEN ERROR: {e}')
                continue
        else:
            print(f'Row {sheet_row}: {first} ({lang_raw}) [skip gen, has email]')

        if args.rows and generated >= args.rows:
            break

        # Translate
        if needs_tr and existing_en:
            try:
                print(f'  Translating to {lang_raw}...')
                tr_body = translate_email(existing_en, lang_raw)
                ws.update(values=[[tr_body]], range_name=f'I{sheet_row}')
                existing_tr = tr_body
                translated += 1
                time.sleep(0.3)
            except Exception as e:
                print(f'  TR ERROR: {e}')

        # Combine
        if existing_en:
            tr = existing_tr.strip() if existing_tr else ''
            if tr:
                combined = tr + '\n\n---\n\n' + existing_en.strip()
            else:
                combined = existing_en.strip()
            ws.update(values=[[combined]], range_name=f'J{sheet_row}')

        if args.rows and generated >= args.rows:
            break

    print(f'\nDone! Generated: {generated} English, Translated: {translated}')
    print(f'Sheet: https://docs.google.com/spreadsheets/d/{get_sheet_id()}/edit#gid={ws.id}')


if __name__ == '__main__':
    main()
