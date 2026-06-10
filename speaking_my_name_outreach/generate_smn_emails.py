"""
generate_smn_emails.py — #SpeakingMyName Campaign Outreach Generator

Reads orgs from the manual SMN xlsx tracker, researches each org, and generates
heavily personalized partnership emails via LLM (DeepSeek / OpenRouter free).

Each email ties the org's SPECIFIC mission and programs to why name inclusion
matters for THEIR community — and why they should participate by June 16th.

Usage:
  python generate_smn_emails.py                    # generate all remaining
  python generate_smn_emails.py --rows 3           # generate only 3 test rows
  python generate_smn_emails.py --row 5            # generate only row 5
  python generate_smn_emails.py --preview          # preview what would happen
  python generate_smn_emails.py --import-only      # only import from xlsx, no LLM
"""

import argparse, io, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Fix Windows encoding for non-Latin characters
if not isinstance(sys.stdout, io.TextIOWrapper) or sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except (ValueError, AttributeError):
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..', 'speakhire-outreach', 'speakhire-outreach-simple'))
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..', 'speakhire-outreach'))

from dotenv import load_dotenv
load_dotenv(r'C:\Users\Tingli\Documents\GitHub\speakhire\autoemail\speakhire-outreach\speakhire-outreach-simple\.env')

import requests, gspread, openpyxl

# ═══════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════
XLSX_PATH = os.path.join(SCRIPT_DIR, '#SpeakingMyName Outreach Tracker(1).xlsx')
XLSX_TAB  = 'OrgsCompaniesAssociations'

SHEET_URL = os.getenv('GOOGLE_SHEET_URL')
CREDS_PATH = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
GSHEET_TAB = '#SpeakingMyName Outreach'
COL_RESEARCH      = 12  # L

# LLM config — use OpenRouter free model if key is set, otherwise DeepSeek
OPENROUTER_KEY = os.getenv('OPENROUTER_API_KEY', '')
if OPENROUTER_KEY:
    API_KEY = OPENROUTER_KEY
    BASE_URL = 'https://openrouter.ai/api/v1'
    LLM_MODEL = 'google/gemma-4-31b-it:free'
else:
    API_KEY = os.getenv('DEEPSEEK_API_KEY')
    BASE_URL = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
    LLM_MODEL = 'deepseek-chat'

# ═══════════════════════════════════════════════════
# SMN CAMPAIGN PROMPT
# ═══════════════════════════════════════════════════

SMN_SYSTEM_PROMPT = """You are an outreach email writer for SpeakHire, a NYC-based nonprofit that helps underrepresented immigrant youth access career opportunities and economic mobility.

SpeakHire runs #SpeakingMyName — a campaign where people record a short video sharing their name, its pronunciation, and the story behind it. The campaign goes LIVE on June 16th (next Monday — just 7 days away). It promotes belonging, respect, and identity inclusion, especially for people whose names are often mispronounced (which disproportionately affects immigrants and people of colour).

The campaign has three steps anyone can join:
1. Record your story — a short video sharing your name, pronunciation, and the story behind it
2. Share on June 16th — post your video and tag others to do the same
3. Lead the movement — show your community that names matter

The campaign already has partners like African Communities Together, Queens Collegiate, and grassroots leaders across NYC.

As David Shapiro, a campaign participant, put it: "There's a story behind how my name is pronounced — a story of heritage, migration, and identity. Saying it correctly is one way we honor where we come from."

YOUR JOB: Read the provided information about an organization, then write a SHORT, HEAVILY PERSONALIZED email asking them to become a #SpeakingMyName campaign partner. A partner commits to:
- Sharing the campaign with their network on or before June 16th
- Encouraging their staff / members / community to record and share name-story videos
- Being recognized on the #SpeakingMyName campaign webpage and social media

PERSONALISATION RULES (THIS IS THE MOST IMPORTANT PART):
1. You MUST tie the org's specific mission, programs, and community to why name inclusion matters for THEIR people. Never write a generic pitch.
2. Reference at least ONE specific program, initiative, or aspect of their mission by name. Show that you know who they are.
3. The connection must feel authentic: "Your organization does X — here's exactly how #SpeakingMyName adds a new dimension to that work."
4. For immigrant-serving orgs: tie name pronunciation to dignity, identity, belonging in a new country.
5. For youth/education orgs: tie to student confidence, cultural pride, anti-bullying.
6. For health/wellness orgs: tie to patient dignity, cultural competence, the importance of being seen correctly.
7. For cultural/community orgs: tie to heritage preservation, identity celebration, cultural pride.
8. For government/civic orgs: tie to constituent dignity, inclusive public service, belonging in civic spaces.
9. NEVER use these banned phrases: "we would be honored," "your commitment to diversity and inclusion," "the important work you do," "we admire your dedication," "exciting opportunity," "unique perspective."
10. NEVER use em dashes (—). Use commas or regular dashes (-) instead.

TONE:
- Mission-driven, warm, specific, grounded. Like someone who actually researched this organization.
- Keep the full email under 180 words. Shorter is better.
- The CTA is participation on June 16th. Be direct but not pushy.
- One exclamation point max. No rhetorical questions as filler.

FOLLOW-UP VS FRESH:
- If this is a FOLLOW-UP email (the prompt will say "FOLLOW-UP: YES"): Acknowledge the earlier contact from our team (Hetal or Hana reached out previously). Briefly reference that conversation. Say something like "I wanted to circle back as the June 16th campaign date is now just days away." Don't re-introduce the entire campaign from scratch — build on the prior contact.
- If this is a FRESH outreach (the prompt will say "FOLLOW-UP: NO"): Give a full but concise introduction to the campaign.

GREETING RULES (important):
- If the contact has a first name, use it: "Hi Lisa," (NOT "Dear Lisa"). Use "Hi" not "Dear" — it's warmer and more personal.
- If the contact has multiple people listed (e.g. "James / Shavone"), greet them both: "Hi James and Shavone,"
- If there is NO named contact person (blank or "Partnerships Team"), use the ORGANIZATION NAME in the greeting: "Hi Queens Community House Team," — NOT "Dear Partnerships Team." Using the org's name shows you know who you're emailing.
- NEVER use "Dear Partnerships Team" or "To whom it may concern" — these make the email feel like spam.

SENDER (use exactly):
The sender is Hana Figueroa, Campaign Coordinator for #SpeakingMyName at SpeakHire. In the email body, use "Hana" (first name only). The full name "Hana Figueroa" goes only in the signature.

Intro line for FRESH emails: "I'm Hana with SpeakHire, a NYC-based nonprofit supporting underrepresented immigrant youth. I'm reaching out about #SpeakingMyName — our campaign going live on June 16th where people share the story behind their name."
Intro line for FOLLOW-UP emails: "I'm Hana with SpeakHire — following up on the conversation our team started with you about #SpeakingMyName earlier this year."

Signature (use exactly):
Best,
Hana Figueroa
Campaign Coordinator, #SpeakingMyName
SpeakHire

SUBJECT LINE RULES:
- Must include the org's name
- Must include "#SpeakingMyName" or "name story"
- Should hint at why this matters for THEIR community
- Keep it under 12 words
- Examples: "Institute of Nonprofit Practice + #SpeakingMyName on June 16", "LGBT Network: name stories as belonging", "Queens Community House youth & #SpeakingMyName"

Return this exact JSON structure (no markdown, no extra text):
{
  "email_subject": "string",
  "email_body": "string - the full email body including greeting and signature"
}"""


# ═══════════════════════════════════════════════════
# WEBSITE RESEARCH — deep scrape for personalization
# ═══════════════════════════════════════════════════

_BOT_HEADERS = {"User-Agent": "Mozilla/5.0 (SpeakHire SMN Outreach Bot; nonprofit use)"}
_TIMEOUT = 12

# Sub-pages likely to contain mission, programs, or DEI info
_MISSION_PATHS = [
    "/about", "/about-us", "/who-we-are", "/our-story", "/mission",
    "/what-we-do", "/our-work", "/programs", "/services", "/initiatives",
    "/impact", "/community", "/diversity", "/dei", "/inclusion",
    "/values", "/our-values", "/who-we-serve",
]


def fetch_url_text(url, timeout=_TIMEOUT):
    """Fetch a webpage and return structured text. Never crashes."""
    result = {"url": url, "title": "", "text": "", "error": ""}
    if not url:
        result["error"] = "No URL provided"
        return result
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url
        result["url"] = url
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(url, timeout=timeout, headers=_BOT_HEADERS, allow_redirects=True)
        resp.raise_for_status()
        result["url"] = resp.url
        soup = BeautifulSoup(resp.text, "html.parser")
        title_tag = soup.find("title")
        if title_tag:
            result["title"] = title_tag.get_text(strip=True)
        for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            t.decompose()
        result["text"] = soup.get_text(separator=" ", strip=True)[:5000]
    except Exception as e:
        result["error"] = str(e)[:200]
    return result


def search_org_website(org_name, org_type="", email_hint=""):
    """Find the org's website using domain pattern guessing + Bing fallback."""
    import urllib.parse

    # Strategy 0: Extract domain from email if it's an org domain (not gmail/yahoo/etc)
    if email_hint and '@' in email_hint:
        email_domain = email_hint.split('@')[-1].strip().lower()
        skip_email_domains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com',
                              'aol.com', 'icloud.com', 'mail.com', 'protonmail.com',
                              'live.com', 'msn.com', 'ymail.com']
        if email_domain not in skip_email_domains:
            # Try the email domain as a website
            for prefix in ['https://www.', 'https://']:
                url = f'{prefix}{email_domain}'
                try:
                    r = requests.get(url, timeout=(1, 2), headers=_BOT_HEADERS, allow_redirects=True)
                    if r.status_code < 400 and len(r.text) > 300:
                        return r.url
                except Exception:
                    pass

    # Strategy 1: Guess common domain patterns from org name (fast + reliable)
    clean = org_name.lower().strip()
    # Remove common suffixes and punctuation for URL generation
    for suffix in [' inc', ' inc.', ' ltd', ' llc', ', inc', ', inc.', ' the ']:
        clean = clean.replace(suffix, '')
    clean = re.sub(r'[^a-z0-9\s]', '', clean)
    clean = re.sub(r'\s+', '', clean)

    # Generate candidate domains
    candidates = []
    if org_type and 'government' in org_type.lower():
        candidates = [
            f"https://www.nyc.gov/site/{clean}",
            f"https://www1.nyc.gov/site/{clean}",
            f"https://council.nyc.gov/{clean}",
        ]
    # Only try top 3 most-likely patterns with fast GET requests
    top_candidates = [
        f"https://www.{clean}.org",
        f"https://{clean}.org",
        f"https://www.{clean}nyc.org",
    ]
    session = requests.Session()
    for url in top_candidates:
        try:
            r = session.get(url, timeout=(1, 2), headers=_BOT_HEADERS, allow_redirects=True)
            if r.status_code < 400 and len(r.text) > 500:
                session.close()
                return r.url  # return final URL after redirects
        except Exception:
            pass
    session.close()

    # Strategy 3: Bing search as last resort
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(
            "https://www.bing.com/search",
            params={"q": f'"{org_name}" site:.org OR site:.com'},
            headers={**_BOT_HEADERS, "Accept-Language": "en-US,en;q=0.9"},
            timeout=10,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        # Look for actual result links (h2 > a pattern in Bing)
        for a_tag in soup.select("h2 a")[:5]:
            href = a_tag.get("href", "")
            if href and "://" in href and "bing.com" not in href:
                domain = urllib.parse.urlparse(href).netloc.lower()
                if not any(s in domain for s in [
                    "facebook.com", "linkedin.com", "instagram.com", "twitter.com",
                    "youtube.com", "wikipedia.org", "idealist.org", "glassdoor.com",
                    "indeed.com", "greatnonprofits.org", "guidestar.org"
                ]):
                    return href
    except Exception:
        pass

    return ""


def _extract_key_info(text, org_name):
    """Extract mission-relevant sentences from page text using keyword scanning."""
    keywords = [
        "mission", "vision", "serve", "community", "program", "diversity",
        "equity", "inclusion", "belonging", "immigrant", "refugee", "youth",
        "education", "health", "justice", "culture", "heritage", "identity",
        "empower", "advocacy", "support", "student", "family", "women",
        "children", "LGBTQ", "queer", "trans", "disability", "access",
        "opportunity", "leadership", "training", "workforce", "economic",
        "nonprofit", "underserved", "marginalized", "BIPOC", "people of color",
    ]
    text_lower = text.lower()
    sentences = [s.strip() for s in text.replace('\n', '. ').split('.') if len(s.strip()) > 30]
    matches = []
    for s in sentences:
        s_lower = s.lower()
        if org_name.lower().split()[0] in s_lower and any(kw in s_lower for kw in keywords):
            matches.append(s[:300])
        elif any(kw in s_lower for kw in keywords[:8]) and any(kw in s_lower for kw in keywords[8:]):
            matches.append(s[:300])
    # Deduplicate by prefix
    seen, unique = set(), []
    for m in matches:
        prefix = m[:40].lower()
        if prefix not in seen:
            seen.add(prefix)
            unique.append(m)
    return unique[:8]


def research_org(org_name, org_type, email_hint=""):
    """
    Deep research on an organization:
    1. Find official website via web search + email domain
    2. Scrape homepage + mission/about pages
    3. Search for DEI/inclusion programs
    4. Return website URL, research summary text, and a structured notes string
    """
    website_url = search_org_website(org_name, org_type, email_hint)

    if not website_url:
        return "", "", f"[No website found for '{org_name}'. Use org name + type ({org_type}) for context.]"

    # --- Step 1: Fetch homepage ---
    homepage = fetch_url_text(website_url)
    if homepage["error"]:
        return website_url, "", f"[Website: {website_url} — homepage could not be fetched: {homepage['error']}]"

    all_texts = [f"HOMEPAGE ({homepage.get('title', 'No title')}):\n{homepage['text']}"]
    visited_urls = {website_url.rstrip("/"), homepage["url"].rstrip("/")}

    # --- Step 2: Fetch mission-related sub-pages ---
    base = website_url.rstrip("/")
    for path in _MISSION_PATHS[:10]:  # try first 10 paths to keep it fast
        sub_url = f"{base}{path}"
        if sub_url.rstrip("/") in visited_urls:
            continue
        visited_urls.add(sub_url.rstrip("/"))
        try:
            page = fetch_url_text(sub_url, timeout=8)
            if not page["error"] and len(page["text"]) > 200:
                label = path.strip("/").replace("-", " ").title()
                all_texts.append(f"[{label}]: {page['text'][:2500]}")
        except Exception:
            pass

    # --- Step 3: Search for DEI/inclusion programs ---
    dei_info = ""
    try:
        import urllib.parse
        from bs4 import BeautifulSoup as BS2
        dei_q = f'"{org_name}" diversity equity inclusion belonging program'
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": dei_q},
            headers=_BOT_HEADERS,
            timeout=10,
        )
        soup2 = BS2(resp.text, "html.parser")
        dei_snippets = []
        for r in soup2.find_all("a", class_="result__snippet")[:3]:
            s = r.get_text(strip=True)
            if len(s) > 40:
                dei_snippets.append(s)
        if dei_snippets:
            dei_info = "\nDEI / INCLUSION SEARCH RESULTS:\n" + "\n".join(f"- {s}" for s in dei_snippets)
    except Exception:
        pass

    # --- Step 4: Build research summary ---
    combined_text = "\n\n---\n\n".join(all_texts)
    key_info = _extract_key_info(combined_text, org_name)

    # Build a concise research notes string for the sheet
    research_notes_parts = [f"URL: {website_url}"]
    if homepage.get("title"):
        research_notes_parts.append(f"Title: {homepage['title']}")

    if key_info:
        for i, info in enumerate(key_info[:5], 1):
            clean_info = info.strip()[:200]
            if clean_info:
                research_notes_parts.append(f"{i}. {clean_info}")

    if dei_info:
        # Include just the first DEI snippet for the notes column
        dei_lines = dei_info.strip().split("\n")
        if dei_lines:
            research_notes_parts.append(f"DEI: {dei_lines[0][:200]}")

    research_notes = "\n".join(research_notes_parts)

    # Build the full research context for the LLM prompt
    context_parts = []
    if key_info:
        context_parts.append("KEY MISSION/PROGRAM INFO:\n" + "\n".join(f"• {k}" for k in key_info[:6]))
    if dei_info:
        context_parts.append(dei_info)

    full_research_text = "\n\n".join(context_parts)[:4000]

    return website_url, full_research_text, research_notes


# ═══════════════════════════════════════════════════
# LLM CALLER
# ═══════════════════════════════════════════════════

def call_llm(system_prompt, user_prompt, max_retries=3):
    """Call DeepSeek/OpenRouter and return parsed JSON, with retry on rate limit."""
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json',
    }
    if OPENROUTER_KEY:
        headers['HTTP-Referer'] = 'https://speakhire.org'
        headers['X-Title'] = 'SpeakHire #SpeakingMyName Outreach'

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

    # Sanitize: strip invisible chars
    content = ''.join(c for c in content if ord(c) >= 32 or c in '\n\r\t')

    try:
        return json.loads(content)
    except json.JSONDecodeError as err:
        # Try aggressive clean: keep only printable ASCII + newlines
        content = ''.join(c for c in content if 32 <= ord(c) <= 126 or c in '\n\r')
        return json.loads(content)


# ═══════════════════════════════════════════════════
# SAFETY HELPERS
# ═══════════════════════════════════════════════════

def safe_str(val):
    """Convert any value to string, handling NaN."""
    if val is None:
        return ''
    if isinstance(val, float):
        return '' if str(val) == 'nan' else str(val)
    return str(val).strip()


def clean_email(text):
    """Clean generated email text of common LLM artifacts."""
    text = text.replace('—', '-').replace('–', '-')  # em/en dashes
    text = text.replace('‘', "'").replace('’', "'")   # smart quotes
    text = text.replace('“', '"').replace('”', '"')
    text = text.replace('…', '...')                        # ellipsis
    # Strip fake-casual tics
    text = re.sub(r', right\?', '?', text)
    text = re.sub(r'\bright\?\b', '', text)
    text = re.sub(r', you know\?', '?', text)
    return text.strip()


# ═══════════════════════════════════════════════════
# XLSX READER
# ═══════════════════════════════════════════════════

def read_orgs_from_xlsx():
    """Read orgs from the SMN xlsx tracker. Returns list of dicts."""
    wb = openpyxl.load_workbook(XLSX_PATH)
    ws = wb[XLSX_TAB]

    # Headers are in row 2 (row 1 is the section title)
    headers = [safe_str(cell.value) for cell in ws[2]]

    orgs = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=3, values_only=True), 3):
        vals = [safe_str(v) for v in row]

        full_name        = vals[0] if len(vals) > 0 else ''
        title            = vals[1] if len(vals) > 1 else ''
        association_name = vals[2] if len(vals) > 2 else ''
        org_type         = vals[3] if len(vals) > 3 else ''
        contact_type     = vals[4] if len(vals) > 4 else ''
        email            = vals[5] if len(vals) > 5 else ''
        reached_out      = vals[6] if len(vals) > 6 else ''
        date             = vals[7] if len(vals) > 7 else ''
        response         = vals[8] if len(vals) > 8 else ''
        notes            = vals[9] if len(vals) > 9 else ''

        # Skip empty rows (no association name)
        if not association_name:
            continue

        # Clean up common spam/non-org entries
        if association_name.lower() in ('n/a', 'none', 'test', ''):
            continue

        # Determine follow-up status
        is_followup = (reached_out.lower() == 'yes' and response.lower() == 'pending')

        orgs.append({
            'xlsx_row': row_idx,
            'full_name': full_name,
            'title': title,
            'association_name': association_name,
            'org_type': org_type,
            'email': email,
            'reached_out': reached_out,
            'response': response,
            'notes': notes,
            'is_followup': is_followup,
        })

    return orgs


# ═══════════════════════════════════════════════════
# GOOGLE SHEET HELPERS
# ═══════════════════════════════════════════════════

def get_sheet():
    """Connect to the Google Sheet and get/create the SMN tab."""
    m = re.search(r'/d/([a-zA-Z0-9\-_]+)', SHEET_URL)
    gc = gspread.service_account(filename=CREDS_PATH)
    sh = gc.open_by_key(m.group(1))
    try:
        ws = sh.worksheet(GSHEET_TAB)
    except Exception:
        ws = sh.add_worksheet(title=GSHEET_TAB, rows='200', cols='12')
        HEADERS = [
            'Full Name', 'Title', 'Association Name', 'Type',
            'Contact Email', 'Status', 'Notes',
            'Email Subject', 'Personalized Email', 'Follow-up?', 'Sent At',
            'Research Notes'
        ]
        ws.update(values=[HEADERS], range_name='A1')
        ws.format('A1:L1', {'textFormat': {'bold': True}})
        ws.freeze(rows=1)
    return ws


def get_existing_rows(ws):
    """Read existing data from the sheet. Returns dict keyed by association name."""
    try:
        all_vals = ws.get_all_values()
    except Exception:
        return {}
    if len(all_vals) < 2:
        return {}

    existing = {}
    for ri, row in enumerate(all_vals[1:], start=2):
        assoc = safe_str(row[2]) if len(row) > 2 else ''
        if assoc:
            existing[assoc.lower().strip()] = {
                'sheet_row': ri,
                'status': safe_str(row[5]) if len(row) > 5 else '',
                'subject': safe_str(row[7]) if len(row) > 7 else '',
                'email_body': safe_str(row[8]) if len(row) > 8 else '',
                'research': safe_str(row[11]) if len(row) > 11 else '',
            }
    return existing


def import_orgs_to_sheet(orgs, ws):
    """Import orgs from xlsx into the Google Sheet (only new ones)."""
    existing = get_existing_rows(ws)
    imported = 0

    for org in orgs:
        key = org['association_name'].lower().strip()
        if key in existing:
            continue  # Already imported

        sheet_row = len(existing) + imported + 2  # 1-indexed, +1 for header
        row_data = [
            org['full_name'],
            org['title'],
            org['association_name'],
            org['org_type'],
            org['email'],
            'READY',  # Status
            org['notes'],
            '',  # Email Subject (to be generated)
            '',  # Personalized Email (to be generated)
            'Yes' if org['is_followup'] else 'No',  # Follow-up?
            '',  # Sent At
            '',  # Research Notes
        ]
        try:
            ws.update(values=[row_data], range_name=f'A{sheet_row}')
            imported += 1
            existing[key] = {'sheet_row': sheet_row, 'status': 'READY', 'subject': '', 'email_body': ''}
        except Exception as e:
            print(f'  ERROR importing {org["association_name"]}: {e}')

    return imported


# ═══════════════════════════════════════════════════
# MAIN: GENERATE EMAILS
# ═══════════════════════════════════════════════════

def generate_email_for_org(org, website_url, website_text, research_notes=""):
    """Generate a personalized SMN email for one org."""

    # Build the user prompt
    followup_label = "FOLLOW-UP: YES" if org['is_followup'] else "FOLLOW-UP: NO"

    # Prior contact context (only for follow-ups)
    prior_context = ""
    if org['is_followup']:
        prior_context = f"""
PRIOR CONTACT CONTEXT:
Our team (Hetal/Hana) reached out to {org['full_name'] or 'the team'} at {org['association_name']} in March 2026.
Current response status: {org['response']}
The email should briefly acknowledge this prior contact and build on it."""

    # Internal notes — always include if available (gives the LLM extra context)
    notes_context = ""
    if org['notes']:
        notes_context = f"""
INTERNAL NOTES (use for personalization if relevant):
{org['notes']}"""

    # Research notes — detailed findings from website scraping
    research_context = ""
    if research_notes:
        research_context = f"""
RESEARCH FINDINGS (reference these specific programs/mission details):
{research_notes}"""

    user_prompt = f"""Write a personalized #SpeakingMyName partnership email for:

ORGANIZATION: {org['association_name']}
TYPE: {org['org_type']}
CONTACT PERSON: {org['full_name'] or 'Partnerships Team'}
CONTACT TITLE: {org['title'] or 'N/A'}
{followup_label}
{prior_context}
{notes_context}
{research_context}

RESEARCH ABOUT THIS ORGANIZATION:
{website_text if website_text else '(No website content available. Use what you know about this organization from its name, type, and mission area to write a personalized email. Focus on why name inclusion matters for the community they serve.)'}
Website: {website_url or 'Not found'}

CRITICAL: Write a personalized email that ties {org['association_name']}'s specific mission and community to why name pronunciation and identity inclusion matters for THEIR people. Reference at least one specific thing about their work. The campaign goes LIVE on June 16th — just days away.

The subject line must include the org name and #SpeakingMyName."""

    try:
        result = call_llm(SMN_SYSTEM_PROMPT, user_prompt)
        subject = clean_email(result.get('email_subject', ''))
        body = clean_email(result.get('email_body', ''))
        return subject, body
    except Exception as e:
        print(f'  LLM ERROR: {e}')
        return '', ''


def main():
    parser = argparse.ArgumentParser(description='Generate #SpeakingMyName outreach emails')
    parser.add_argument('--rows', type=int, help='Generate only first N rows')
    parser.add_argument('--row', type=int, help='Generate only a specific xlsx row number')
    parser.add_argument('--preview', action='store_true', help='Preview orgs without calling LLM')
    parser.add_argument('--import-only', action='store_true', help='Only import from xlsx, skip generation')
    parser.add_argument('--force', action='store_true', help='Regenerate even if already DRAFTED')
    parser.add_argument('--research-only', action='store_true', help='Only research orgs and store findings, skip email generation')
    args = parser.parse_args()

    # Read orgs from xlsx
    orgs = read_orgs_from_xlsx()
    print(f'Read {len(orgs)} orgs from "{XLSX_TAB}" tab')
    print(f'Using: {LLM_MODEL} via {BASE_URL}')
    print()

    if args.preview:
        for i, org in enumerate(orgs):
            fw = ' [FOLLOW-UP]' if org['is_followup'] else ''
            print(f'  {i+1}. {org["association_name"]} ({org["org_type"]}){fw}')
            print(f'     Contact: {org["full_name"]} | {org["email"]}')
            if org['notes']:
                print(f'     Notes: {org["notes"][:120]}')
        return

    # Connect to Google Sheet
    print('Connecting to Google Sheet...')
    ws = get_sheet()

    # Import orgs into sheet
    print('Importing orgs into sheet...')
    imported = import_orgs_to_sheet(orgs, ws)
    print(f'  {imported} new orgs imported')

    if args.import_only:
        print('Import-only mode — done.')
        return

    # Refresh existing rows after import
    existing = get_existing_rows(ws)

    # --- Build work list: orgs that need processing ---
    work_items = []
    for org in orgs:
        key = org['association_name'].lower().strip()
        sheet_info = existing.get(key, {})
        sheet_row = sheet_info.get('sheet_row', 0)
        current_status = sheet_info.get('status', '')

        # Skip if already generated (unless forced or research-only)
        if current_status in ('DRAFTED', 'APPROVED', 'SENT') and not args.force and not args.research_only:
            continue
        # Row filter
        if args.row is not None and org['xlsx_row'] != args.row:
            continue

        work_items.append({
            'org': org,
            'key': key,
            'sheet_row': sheet_row,
            'current_status': current_status,
        })

    if args.rows:
        work_items = work_items[:args.rows]

    if not work_items:
        print('No orgs to process.')
        return

    print(f'Processing {len(work_items)} orgs...')
    print()

    # --- Phase 1: Research all orgs in parallel ---
    print('=== PHASE 1: Researching orgs (parallel) ===')
    research_results = {}  # key -> (website_url, website_text, research_notes)

    def _research_one(item):
        org = item['org']
        url, text, notes = research_org(org['association_name'], org['org_type'], org.get('email', ''))
        return item['key'], (url, text, notes)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_research_one, item): item for item in work_items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                key, result = future.result()
                research_results[key] = result
                url, text, notes = result
                org = item['org']
                fw = ' [FOLLOW-UP]' if org['is_followup'] else ''
                status_icon = '✓' if url else '✗'
                print(f'{status_icon} {org["association_name"]} ({org["org_type"]}){fw}{" → " + url if url else ""}')
            except Exception as e:
                print(f'✗ ERROR: {item["org"]["association_name"]} — {e}')
                research_results[item['key']] = ('', '', '')

    # --- Write research notes to sheet ---
    print()
    print('Writing research to sheet...')
    for item in work_items:
        key = item['key']
        sheet_row = item['sheet_row']
        if sheet_row and key in research_results:
            _, _, notes = research_results[key]
            if notes:
                try:
                    ws.update(values=[[notes]], range_name=f'L{sheet_row}')
                    if args.research_only:
                        ws.update(values=[[f'RESEARCHED']], range_name=f'F{sheet_row}')
                except Exception:
                    pass

    if args.research_only:
        print(f'Research-only mode — {len(work_items)} orgs researched.')
        sheet_id = re.search(r'/d/([a-zA-Z0-9\-_]+)', SHEET_URL).group(1)
        print(f'Sheet: https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={ws.id}')
        return

    # --- Phase 2: Generate emails sequentially (LLM rate limits) ---
    print()
    print('=== PHASE 2: Generating emails ===')
    generated = 0
    for item in work_items:
        org = item['org']
        sheet_row = item['sheet_row']
        key = item['key']

        website_url, website_text, research_notes = research_results.get(key, ('', '', ''))

        fw = ' [FOLLOW-UP]' if org['is_followup'] else ''
        print(f'Row {org["xlsx_row"]}: {org["association_name"]} ({org["org_type"]}){fw}')

        # Generate email
        print(f'  Generating email...')
        subject, body = generate_email_for_org(org, website_url, website_text, research_notes)

        if not subject or not body:
            print(f'  GENERATION FAILED — skipping')
            continue

        print(f'  Subject: {subject[:80]}')
        print(f'  Body length: {len(body)} chars')

        # Write to sheet
        if sheet_row:
            try:
                ws.update(values=[[
                    org['full_name'], org['title'], org['association_name'],
                    org['org_type'], org['email'], 'DRAFTED', org['notes'],
                    subject, body,
                    'Yes' if org['is_followup'] else 'No', '',
                    research_notes,
                ]], range_name=f'A{sheet_row}')
                print(f'  Written to sheet row {sheet_row}')
            except Exception as e:
                print(f'  WRITE ERROR: {e}')
                continue

        generated += 1
        time.sleep(0.5)  # Rate limit between LLM calls

    print(f'\nDone! Generated: {generated} emails')
    sheet_id = re.search(r'/d/([a-zA-Z0-9\-_]+)', SHEET_URL).group(1)
    print(f'Sheet: https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={ws.id}')


if __name__ == '__main__':
    main()
