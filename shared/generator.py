"""
shared/generator.py — Common engine for all email generation scripts.

Every campaign's generate_*.py imports from here. The campaign script only
needs to define: data reading, research, and prompt building.
Everything else (LLM calling, text cleaning, sheet writing, CLI) lives here.

Usage in a campaign script:
    from shared.config import *
    from shared.generator import *
"""

import argparse, io, json, os, re, sys, time

from shared.config import (
    API_KEY, BASE_URL, LLM_MODEL, OPENROUTER_KEY,
    SHEET_URL, CREDS_PATH, BOT_HEADERS,
)

# ═══════════════════════════════════════════════════
# WINDOWS ENCODING FIX
# ═══════════════════════════════════════════════════

def fix_windows_encoding():
    """Fix stdout encoding on Windows so non-Latin characters don't crash."""
    if not isinstance(sys.stdout, io.TextIOWrapper) or sys.stdout.encoding != 'utf-8':
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        except (ValueError, AttributeError):
            pass


# ═══════════════════════════════════════════════════
# CLI ARGS
# ═══════════════════════════════════════════════════

def parse_args(description="Generate outreach emails", extra_args=None):
    """Standard CLI parser. Every campaign gets --rows, --row, --preview, --force.
    Pass extra_args as a list of argparse config dicts for campaign-specific flags.

    Example:
        args = parse_args("Generate Soiree emails", extra_args=[
            {"name": "--csv", "required": True, "help": "CSV file of contacts"},
            {"name": "--type", "required": True, "choices": ["sponsor", "individual"]},
        ])
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--rows', type=int, help='Generate only first N rows')
    parser.add_argument('--row', type=int, help='Generate only a specific row')
    parser.add_argument('--preview', action='store_true', help='Preview contacts without calling LLM')
    parser.add_argument('--force', action='store_true', help='Regenerate even if already drafted')
    if extra_args:
        for ea in extra_args:
            kwargs = dict(ea)
            name = kwargs.pop('name')
            parser.add_argument(name, **kwargs)
    return parser.parse_args()


# ═══════════════════════════════════════════════════
# LLM CALLER
# ═══════════════════════════════════════════════════

def call_llm(system_prompt, user_prompt, max_retries=3):
    """Call the LLM and return parsed JSON. Retries on rate limit with backoff."""
    import requests

    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json',
    }
    if OPENROUTER_KEY:
        headers['HTTP-Referer'] = 'https://speakhire.org'
        headers['X-Title'] = 'SpeakHire Outreach'

    for attempt in range(max_retries):
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
            timeout=90,
        )
        if resp.status_code == 429:
            wait = (2 ** attempt) * 3  # 3s, 6s, 12s
            print(f'  Rate limited, waiting {wait}s...')
            time.sleep(wait)
            continue
        resp.raise_for_status()
        break

    content = resp.json()['choices'][0]['message']['content'].strip()

    # Clean markdown wrappers
    if content.startswith('```'):
        content = content[content.find('\n'):].strip()
    if content.endswith('```'):
        content = content[:-3].strip()

    # Extract JSON
    s, e = content.find('{'), content.rfind('}')
    if s != -1 and e != -1:
        content = content[s:e+1]

    # Sanitize invisible characters
    content = ''.join(c for c in content if ord(c) >= 32 or c in '\n\r\t')

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Fallback: ASCII-only
        content = ''.join(c for c in content if 32 <= ord(c) <= 126 or c in '\n\r')
        return json.loads(content)


# ═══════════════════════════════════════════════════
# TEXT HELPERS
# ═══════════════════════════════════════════════════

def safe_str(val):
    """Convert any value to string, handling NaN and None from sheets/xlsx."""
    if val is None:
        return ''
    if isinstance(val, float):
        return '' if str(val) == 'nan' else str(val)
    return str(val).strip()


def clean_email(text):
    """Clean LLM-generated email of artifacts: em dashes, smart quotes, fake-casual tics."""
    if not text:
        return ''
    text = text.replace('—', '-').replace('–', '-')   # em/en dashes
    text = text.replace('‘', "'").replace('’', "'")   # smart quotes
    text = text.replace('“', '"').replace('”', '"')
    text = text.replace('…', '...')                        # ellipsis
    text = re.sub(r', right\?', '?', text)                      # fake-casual tics
    text = re.sub(r'\bright\?\b', '', text)
    text = re.sub(r', you know\?', '?', text)
    return text.strip()


# ═══════════════════════════════════════════════════
# SHEET HELPERS
# ═══════════════════════════════════════════════════

def get_sheet_id():
    """Extract the Google Sheet ID from SHEET_URL."""
    m = re.search(r'/d/([a-zA-Z0-9\-_]+)', SHEET_URL)
    return m.group(1) if m else None


def connect_sheet(tab_name, headers=None):
    """Connect to a Google Sheet tab. Creates it with headers if missing.
    Returns the gspread Worksheet."""
    import gspread

    gc = gspread.service_account(filename=CREDS_PATH)
    sh = gc.open_by_key(get_sheet_id())

    try:
        return sh.worksheet(tab_name)
    except Exception:
        if headers is None:
            headers = [
                'Contact Name', 'Title', 'Organization', 'Email',
                'Campaign Type', 'Status', 'Notes',
                'Email Subject', 'Personalized Email',
                'Research Notes', 'Sent At', 'Extra',
            ]
        num_cols = len(headers)
        ws = sh.add_worksheet(title=tab_name, rows='500', cols=str(num_cols))
        ws.update(values=[headers], range_name='A1')
        last_col = chr(64 + num_cols) if num_cols <= 26 else 'Z'
        ws.format(f'A1:{last_col}1', {'textFormat': {'bold': True}})
        ws.freeze(rows=1)
        return ws


def sheet_link():
    """Return a clickable Google Sheet URL with gid."""
    # gid is only available after connect_sheet() has been called.
    return f'https://docs.google.com/spreadsheets/d/{get_sheet_id()}/edit'
