"""
generate_summit_emails.py
One command: python generate_summit_emails.py

Reads Summit Outreach 2026 tab from Google Sheets. For every row without a
personalized email, calls DeepSeek to generate it (English). If the person
speaks another language, also generates a translation.

Usage:
  python generate_summit_emails.py           # generate all remaining
  python generate_summit_emails.py --rows 3  # generate only 3 test rows
  python generate_summit_emails.py --row 5   # generate only row 5
"""

import argparse
import io
import json
import os
import re
import sys
import time

# Fix Windows encoding for non-Latin characters (only if not already utf-8)
if not isinstance(sys.stdout, io.TextIOWrapper) or sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except (ValueError, AttributeError):
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..', 'speakhire-outreach', 'speakhire-outreach-simple'))

from dotenv import load_dotenv
load_dotenv(r'C:\Users\Tingli\Documents\GitHub\speakhire\autoemail\speakhire-outreach\speakhire-outreach-simple\.env')

import requests
import gspread
from summit_prompt import SUMMIT_SYSTEM_PROMPT, SUMMIT_CONTEXT

# Use OpenRouter free model if key is set, otherwise fall back to DeepSeek
OPENROUTER_KEY = os.getenv('OPENROUTER_API_KEY', '')
if OPENROUTER_KEY:
    API_KEY = OPENROUTER_KEY
    BASE_URL = 'https://openrouter.ai/api/v1'
    LLM_MODEL = 'google/gemma-4-31b-it:free'
else:
    API_KEY = os.getenv('DEEPSEEK_API_KEY')
    BASE_URL = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
    LLM_MODEL = 'deepseek-chat'

SHEET_URL = os.getenv('GOOGLE_SHEET_URL')
CREDS_PATH = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')


def read_contacts():
    m = re.search(r'/d/([a-zA-Z0-9\-_]+)', SHEET_URL)
    gc = gspread.service_account(filename=CREDS_PATH)
    sh = gc.open_by_key(m.group(1))
    ws = sh.worksheet('Summit Outreach 2026')
    return ws.get_all_records(), ws


def call_llm(system_prompt, user_prompt):
    """Call DeepSeek and return parsed JSON."""
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json',
    }
    # OpenRouter requires these headers
    if OPENROUTER_KEY:
        headers['HTTP-Referer'] = 'https://speakhire.org'
        headers['X-Title'] = 'SpeakHire Summit Outreach'

    resp = requests.post(
        f'{BASE_URL}/chat/completions',
        headers=headers,
        json={
            'model': LLM_MODEL,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'temperature': 0.7,
        },
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()['choices'][0]['message']['content'].strip()

    if content.startswith('```'):
        content = content[content.find('\n'):].strip()
    if content.endswith('```'):
        content = content[:-3].strip()
    s, e = content.find('{'), content.rfind('}')
    if s != -1 and e != -1:
        content = content[s:e+1]
    # Sanitize: strip truly invisible chars, escape literal newlines in JSON strings
    content = ''.join(c for c in content if ord(c) >= 32 or c in '\n\r\t')
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        # If it still fails, dump the problematic area and try stripping all non-ASCII
        start = max(0, e.pos - 20)
        end = min(len(content), e.pos + 20)
        problem = content[start:end]
        # Try aggressive clean: keep only printable ASCII + newlines
        content = ''.join(c for c in content if 32 <= ord(c) <= 126 or c in '\n\r')
        return json.loads(content)


def safe_str(val):
    """Convert any value to string, handling NaN floats from sheets."""
    if val is None: return ''
    if isinstance(val, float): return '' if str(val) == 'nan' else str(val)
    return str(val).strip()

def generate_email(contact):
    """Generate a personalized summit email in English."""
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

Remember: mention at least TWO speakers that match their interests. Mention at least ONE non-speaker summit feature. If they speak another language, flag it as an asset. Keep it under 130 words. NO em dashes.
"""
    result = call_llm(SUMMIT_SYSTEM_PROMPT, user_prompt)
    body = result.get('email_body', '').replace('—', '-').replace('–', '-')
    # Strip fake-casual tics
    body = re.sub(r', right\?', '?', body)  # "you speak French, right?" -> "you speak French?"
    body = re.sub(r'\bright\?\b', '', body)  # standalone "right?"
    body = re.sub(r', you know\?', '?', body)
    subject = result.get('email_subject', '').replace('—', '-').replace('–', '-')
    print(f'  Subject: {subject}')
    return body, subject


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


def translate_email(email_body, language):
    """Translate an email into the target language."""
    system = TRANSLATION_SYSTEM_PROMPT.replace('{language}', language)
    user = f"Translate this email into {language}:\n\n{email_body}"

    try:
        result = call_llm(system, user)
        translated = result.get('translated_email', email_body)
    except Exception:
        # Fallback: call LLM without JSON structure, get raw translation
        raw_system = f'Translate the following email into {language}. Keep all names, URLs, and company names exactly as-is. Return ONLY the translated text, no JSON, no markdown.'
        resp = requests.post(
            f'{BASE_URL}/chat/completions',
            headers={'Authorization': f'Bearer {API_KEY}', 'Content-Type': 'application/json', 'HTTP-Referer': 'https://speakhire.org', 'X-Title': 'SpeakHire Summit'},
            json={
                'model': LLM_MODEL,
                'messages': [
                    {'role': 'system', 'content': raw_system},
                    {'role': 'user', 'content': email_body},
                ],
                'temperature': 0.7,
            },
            timeout=60,
        )
        resp.raise_for_status()
        translated = resp.json()['choices'][0]['message']['content'].strip()

    translated = translated.replace('—', '-').replace('–', '-')
    translated = ''.join(c for c in translated if ord(c) >= 32 or c in '\n\r\t')
    return translated


def main():
    parser = argparse.ArgumentParser(description='Generate Summit outreach emails via DeepSeek')
    parser.add_argument('--rows', type=int, help='Generate only first N empty rows')
    parser.add_argument('--row', type=int, help='Generate only a specific sheet row number')
    parser.add_argument('--translate-only', action='store_true', help='Only translate existing emails, skip generation')
    args = parser.parse_args()

    contacts, ws = read_contacts()
    print(f'Read {len(contacts)} contacts from Summit Outreach 2026')
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

        needs_gen = not existing_en
        needs_tr = lang and lang not in ('english', 'nan', '') and not existing_tr

        if not needs_gen and not needs_tr:
            continue

        if args.row is not None and sheet_row != args.row:
            continue

        # --- Generate English ---
        if needs_gen and not args.translate_only:
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

        # --- Translate ---
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

        # --- Combine ---
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
    print(f'Sheet: {SHEET_URL}')


if __name__ == '__main__':
    main()
