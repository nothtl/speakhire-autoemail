"""
generate_soiree_emails.py — SpeakHire Soiree 2026 Outreach Generator

Generates personalized emails for TWO campaign types:
  - sponsor:    Ask companies/orgs to sponsor the Soiree (tiers: $5K–$50K+)
  - individual: Invite people to attend the Soiree ($150/ticket)

Accepts a CSV file of contacts (you'll provide the list). Researches each
company/org, generates a personalized email via LLM, and writes to a
"Soiree Outreach" tab in the Google Sheet.

Usage:
  python generate_soiree_emails.py --csv sponsors.csv --type sponsor
  python generate_soiree_emails.py --csv attendees.csv --type individual
  python generate_soiree_emails.py --csv sponsors.csv --type sponsor --rows 5
  python generate_soiree_emails.py --csv list.csv --type sponsor --preview
"""

import argparse, csv, io, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

if not isinstance(sys.stdout, io.TextIOWrapper) or sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except (ValueError, AttributeError):
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..', 'speakhire-outreach', 'speakhire-outreach-simple'))
sys.path.insert(0, SCRIPT_DIR)

from dotenv import load_dotenv
load_dotenv(r'C:\Users\Tingli\Documents\GitHub\speakhire\autoemail\speakhire-outreach\speakhire-outreach-simple\.env')

import requests, gspread
from soiree_prompt import get_prompt, SOIREE_DATE, SOIREE_TIME, SOIREE_VENUE, SOIREE_TAGLINE, SOIREE_TICKET, SOIREE_SPONSOR_TIERS, SOIREE_HIGHLIGHTS

# ═══════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════

SHEET_URL = os.getenv('GOOGLE_SHEET_URL')
CREDS_PATH = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
GSHEET_TAB = 'Soiree Outreach'

OPENROUTER_KEY = os.getenv('OPENROUTER_API_KEY', '')
if OPENROUTER_KEY:
    API_KEY = OPENROUTER_KEY
    BASE_URL = 'https://openrouter.ai/api/v1'
    LLM_MODEL = 'google/gemma-4-31b-it:free'
else:
    API_KEY = os.getenv('DEEPSEEK_API_KEY')
    BASE_URL = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
    LLM_MODEL = 'deepseek-chat'

_BOT_HEADERS = {"User-Agent": "Mozilla/5.0 (SpeakHire Soiree Bot; nonprofit use)"}

# Column indices for the Soiree Outreach tab (12 cols, A-L)
COL_NAME        = 1   # A: Contact Name
COL_TITLE       = 2   # B: Job Title
COL_ORG         = 3   # C: Organization/Company
COL_EMAIL       = 4   # D: Email
COL_TYPE        = 5   # E: Campaign Type (sponsor/individual)
COL_STATUS      = 6   # F: Status
COL_NOTES       = 7   # G: Notes / Profile
COL_SUBJECT     = 8   # H: Email Subject
COL_BODY        = 9   # I: Personalized Email
COL_RESEARCH    = 10  # J: Research Notes
COL_SENT_AT     = 11  # K: Sent At
COL_EXTRA       = 12  # L: Extra (languages, interests, etc.)


# ═══════════════════════════════════════════════════
# CSV READER
# ═══════════════════════════════════════════════════

def read_csv(filepath):
    """Read contacts from CSV. Auto-detects columns for sponsor vs individual."""
    contacts = []
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        headers = [h.lower().strip() for h in (reader.fieldnames or [])]

        for row in reader:
            name = row.get('name', row.get('full name', row.get('Name', row.get('Full Name',
                   row.get('first name', row.get('First Name', '')))))).strip()
            # Also check for separate first/last name columns
            first = row.get('first name', row.get('First Name', row.get('first_name', ''))).strip()
            last = row.get('last name', row.get('Last Name', row.get('last_name', ''))).strip()
            if first and last:
                name = f"{first} {last}"
            elif first:
                name = first

            email_cols = ['email', 'Email', 'EMAIL', 'contact email', 'Contact Email']
            email = next((row.get(c, '').strip() for c in email_cols if row.get(c, '').strip()), '')

            org_cols = ['organization', 'organisation', 'company', 'Company', 'org', 'Org',
                        'org_name', 'association name', 'Association Name']
            org = next((row.get(c, '').strip() for c in org_cols if row.get(c, '').strip()), '')

            title_cols = ['title', 'job title', 'Title', 'Job Title', 'position', 'Position']
            title = next((row.get(c, '').strip() for c in title_cols if row.get(c, '').strip()), '')

            # Profile fields for individuals
            languages = row.get('languages', row.get('Languages', row.get('language', ''))).strip()
            interest_cols = ['career interests', 'career_interests', 'interests', 'Career Interests']
            interests = next((row.get(c, '').strip() for c in interest_cols if row.get(c, '').strip()), '')

            field_cols = ['career field', 'career_field', 'main_career_field', 'Career Field']
            career_field = next((row.get(c, '').strip() for c in field_cols if row.get(c, '').strip()), '')
            notes = row.get('notes', row.get('Notes', row.get('NOTES', ''))).strip()

            if not name and not org:
                continue  # skip empty rows

            contacts.append({
                'name': name,
                'email': email,
                'org': org,
                'title': title,
                'languages': languages,
                'interests': interests,
                'career_field': career_field,
                'notes': notes,
            })

    return contacts


# ═══════════════════════════════════════════════════
# WEBSITE RESEARCH (same approach as SMN script)
# ═══════════════════════════════════════════════════

def fetch_url_text(url, timeout=10):
    result = {"url": url, "title": "", "text": "", "error": ""}
    if not url: return result
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url
        result["url"] = url
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(url, timeout=timeout, headers=_BOT_HEADERS, allow_redirects=True)
        resp.raise_for_status()
        result["url"] = resp.url
        soup = BeautifulSoup(resp.text, "html.parser")
        t = soup.find("title")
        if t: result["title"] = t.get_text(strip=True)
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        result["text"] = soup.get_text(separator=" ", strip=True)[:5000]
    except Exception as e:
        result["error"] = str(e)[:200]
    return result


def search_org_website(org_name, email_hint=""):
    """Find an org's website via domain guessing + email extraction."""
    # Strategy 0: Extract from email
    if email_hint and '@' in email_hint:
        email_domain = email_hint.split('@')[-1].strip().lower()
        skip = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'aol.com',
                'icloud.com', 'mail.com', 'protonmail.com', 'live.com', 'msn.com']
        if email_domain not in skip:
            for prefix in ['https://www.', 'https://']:
                try:
                    r = requests.get(f'{prefix}{email_domain}', timeout=(1, 2), headers=_BOT_HEADERS, allow_redirects=True)
                    if r.status_code < 400 and len(r.text) > 300:
                        return r.url
                except Exception:
                    pass

    # Strategy 1: Domain patterns
    clean = re.sub(r'[^a-z0-9\s]', '', org_name.lower().strip())
    clean = re.sub(r'\s+', '', clean)
    for url in [f"https://www.{clean}.org", f"https://{clean}.org", f"https://www.{clean}.com"]:
        try:
            r = requests.get(url, timeout=(1, 2), headers=_BOT_HEADERS, allow_redirects=True)
            if r.status_code < 400 and len(r.text) > 300:
                return r.url
        except Exception:
            pass
    return ""


def research_org(org_name, email_hint=""):
    """Research an org's website and extract mission/program info."""
    website_url = search_org_website(org_name, email_hint)
    if not website_url:
        return "", "", f"[No website found for '{org_name}']"

    page = fetch_url_text(website_url)
    if page["error"]:
        return website_url, "", f"[Website: {website_url} — could not fetch]"

    # Extract key sentences mentioning mission/programs
    text = page["text"]
    keywords = ["mission", "program", "community", "serve", "diversity", "inclusion",
                "equity", "belonging", "impact", "support", "partner", "initiative",
                "csr", "philanthropy", "grant", "workforce", "education", "youth"]
    sentences = [s.strip() for s in text.replace('\n', '. ').split('.') if len(s.strip()) > 30]
    matches = []
    for s in sentences:
        s_lower = s.lower()
        if any(kw in s_lower for kw in keywords[:6]) and len(s) > 40:
            matches.append(s[:250])

    # Build notes
    notes_parts = [f"URL: {website_url}"]
    if page.get("title"):
        notes_parts.append(f"Title: {page['title']}")
    for i, m in enumerate(matches[:5], 1):
        notes_parts.append(f"{i}. {m.strip()}")

    research_notes = "\n".join(notes_parts)
    research_text = "\n".join(f"• {m}" for m in matches[:6])[:4000] if matches else ""

    return website_url, research_text, research_notes


# ═══════════════════════════════════════════════════
# LLM CALLER
# ═══════════════════════════════════════════════════

def call_llm(system_prompt, user_prompt, max_retries=3):
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json',
    }
    if OPENROUTER_KEY:
        headers['HTTP-Referer'] = 'https://speakhire.org'
        headers['X-Title'] = 'SpeakHire Soiree Outreach'

    for attempt in range(max_retries):
        resp = requests.post(f'{BASE_URL}/chat/completions', headers=headers, json={
            'model': LLM_MODEL,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'temperature': 0.7,
        }, timeout=90)
        if resp.status_code == 429:
            wait = (2 ** attempt) * 3
            print(f'  Rate limited, waiting {wait}s...')
            time.sleep(wait)
            continue
        resp.raise_for_status()
        break

    content = resp.json()['choices'][0]['message']['content'].strip()
    if content.startswith('```'): content = content[content.find('\n'):].strip()
    if content.endswith('```'): content = content[:-3].strip()
    s, e = content.find('{'), content.rfind('}')
    if s != -1 and e != -1: content = content[s:e+1]
    content = ''.join(c for c in content if ord(c) >= 32 or c in '\n\r\t')
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        content = ''.join(c for c in content if 32 <= ord(c) <= 126 or c in '\n\r')
        return json.loads(content)


def clean(text):
    if not text: return ''
    text = text.replace('—', '-').replace('–', '-').replace('‘', "'").replace('’', "'")
    text = text.replace('“', '"').replace('”', '"').replace('…', '...')
    text = re.sub(r', right\?', '?', text)
    text = re.sub(r'\bright\?\b', '', text)
    return text.strip()


# ═══════════════════════════════════════════════════
# GOOGLE SHEET
# ═══════════════════════════════════════════════════

def get_sheet():
    m = re.search(r'/d/([a-zA-Z0-9\-_]+)', SHEET_URL)
    gc = gspread.service_account(filename=CREDS_PATH)
    sh = gc.open_by_key(m.group(1))
    try:
        return sh.worksheet(GSHEET_TAB)
    except Exception:
        ws = sh.add_worksheet(title=GSHEET_TAB, rows='500', cols='12')
        HEADERS = ['Contact Name', 'Title', 'Organization', 'Email',
                   'Campaign Type', 'Status', 'Notes',
                   'Email Subject', 'Personalized Email',
                   'Research Notes', 'Sent At', 'Extra']
        ws.update(values=[HEADERS], range_name='A1')
        ws.format('A1:L1', {'textFormat': {'bold': True}})
        ws.freeze(rows=1)
        return ws


# ═══════════════════════════════════════════════════
# EMAIL GENERATION
# ═══════════════════════════════════════════════════

def build_user_prompt(contact, campaign_type, website_url, research_text):
    """Build the LLM user prompt based on campaign type."""
    name = contact.get('name', '') or 'there'
    org = contact.get('org', '') or 'your organization'
    title = contact.get('title', '')
    email = contact.get('email', '')
    notes = contact.get('notes', '')
    languages = contact.get('languages', '')
    interests = contact.get('interests', '')
    career_field = contact.get('career_field', '')

    if campaign_type == 'sponsor':
        research_block = f"""
COMPANY RESEARCH:
{research_text if research_text else '(No website content found. Use the company name and what you know about their industry to write a personalized sponsorship ask.)'}
Website: {website_url or 'Not found'}"""

        return f"""Write a personalized Soiree SPONSORSHIP email for:

COMPANY: {org}
CONTACT: {name} {f'({title})' if title else ''}
{f'NOTES: {notes}' if notes else ''}
{research_block}

The Soiree is on {SOIREE_DATE} at {SOIREE_VENUE}. Tiers: $5K–$50K+.
CRITICAL: Reference at least ONE specific program, initiative, or fact about {org}. Tie it to why SpeakHire's mission of launching immigrant/first-gen careers matters to THEM."""

    elif campaign_type == 'individual':
        profile = []
        if title: profile.append(f"Job: {title}")
        if org: profile.append(f"Company: {org}")
        if career_field: profile.append(f"Career field: {career_field}")
        if languages: profile.append(f"Languages: {languages}")
        if interests: profile.append(f"Interests: {interests}")
        profile_text = '\n'.join(profile) if profile else '(minimal profile — write a warm, brief invitation focused on the event)'

        return f"""Write a personalized Soiree INVITATION for:

NAME: {name}
EMAIL: {email or 'N/A'}
{profile_text}
{f'NOTES: {notes}' if notes else ''}

The Soiree is {SOIREE_DATE}, {SOIREE_TIME} at {SOIREE_VENUE}.
CRITICAL: Make this feel personal to {name}. Use whatever details are available. If minimal info, write a shorter, sincere invitation focused on the experience itself."""

    else:
        raise ValueError(f"Unknown type: {campaign_type}")


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='Generate Soiree outreach emails')
    parser.add_argument('--csv', required=True, help='CSV file of contacts')
    parser.add_argument('--type', required=True, choices=['sponsor', 'individual'],
                        help='Campaign type: sponsor or individual')
    parser.add_argument('--rows', type=int, help='Process only first N rows')
    parser.add_argument('--preview', action='store_true', help='Preview without generating')
    args = parser.parse_args()

    contacts = read_csv(args.csv)
    print(f'Read {len(contacts)} contacts from {args.csv}')
    print(f'Campaign: {args.type}')
    print(f'Model: {LLM_MODEL}')
    print()

    if args.rows:
        contacts = contacts[:args.rows]

    if args.preview:
        for i, c in enumerate(contacts):
            print(f'{i+1}. {c["name"] or "?"} | {c["org"] or "?"} | {c["email"] or "?"}')
            if c.get('notes'): print(f'   Notes: {c["notes"][:100]}')
        return

    # Connect to sheet
    print('Connecting to Google Sheet...')
    ws = get_sheet()
    system_prompt = get_prompt(args.type)

    generated = 0
    for i, contact in enumerate(contacts):
        org_name = contact.get('org', '') or contact.get('name', '?')
        print(f'{i+1}/{len(contacts)}: {contact["name"] or org_name}')

        # Research (sponsor only — for individuals, use profile)
        website_url = ""; research_text = ""; research_notes = ""
        if args.type == 'sponsor' and contact.get('org'):
            print(f'  Researching {contact["org"]}...')
            website_url, research_text, research_notes = research_org(contact['org'], contact.get('email', ''))
            if website_url: print(f'    → {website_url}')

        # Generate email
        print(f'  Generating...')
        user_prompt = build_user_prompt(contact, args.type, website_url, research_text)
        try:
            result = call_llm(system_prompt, user_prompt)
            subject = clean(result.get('email_subject', ''))
            body = clean(result.get('email_draft', ''))
        except Exception as e:
            print(f'  LLM ERROR: {e}')
            continue

        if not body:
            print(f'  EMPTY BODY — skipping')
            continue

        print(f'  Subject: {subject[:80]}')

        # Write to sheet
        extra = ''
        if args.type == 'individual':
            extras = []
            if contact.get('languages'): extras.append(f"Languages: {contact['languages']}")
            if contact.get('interests'): extras.append(f"Interests: {contact['interests']}")
            if contact.get('career_field'): extras.append(f"Field: {contact['career_field']}")
            extra = '; '.join(extras)

        sheet_row = i + 2
        try:
            ws.update(values=[[
                contact['name'], contact.get('title', ''), contact.get('org', ''),
                contact.get('email', ''), args.type, 'DRAFTED', contact.get('notes', ''),
                subject, body, research_notes, '', extra,
            ]], range_name=f'A{sheet_row}')
            print(f'  Written to sheet row {sheet_row}')
        except Exception as e:
            print(f'  WRITE ERROR: {e}')
            continue

        generated += 1
        time.sleep(0.5)

    print(f'\nDone! Generated: {generated} emails')
    sheet_id = re.search(r'/d/([a-zA-Z0-9\-_]+)', SHEET_URL).group(1)
    print(f'Sheet: https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={ws.id}')


if __name__ == '__main__':
    main()
