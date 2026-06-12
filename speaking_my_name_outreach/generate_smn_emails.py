"""
generate_smn_emails.py — #SpeakingMyName Campaign Email Generator

Reads orgs from the Excel tracker, researches each org's website (parallel),
generates personalized partnership emails via LLM, writes to Google Sheets.

Usage:
  python generate_smn_emails.py                    # generate all remaining
  python generate_smn_emails.py --rows 3           # generate only 3 test rows
  python generate_smn_emails.py --row 5            # generate only row 5
  python generate_smn_emails.py --preview          # preview what would happen
  python generate_smn_emails.py --research-only    # only research, no generation
  python generate_smn_emails.py --import-only      # only import from xlsx to sheet
"""

# ═══════════════════════════════════════════════════
# IMPORTS
# ═══════════════════════════════════════════════════

import os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..'))

from shared.config import BOT_HEADERS, SHEET_URL, CREDS_PATH, LLM_MODEL, BASE_URL
from shared.generator import (
    fix_windows_encoding, parse_args, call_llm,
    clean_email, safe_str, connect_sheet, get_sheet_id,
)
from smn_prompt import get_prompt, CAMPAIGN

fix_windows_encoding()

import requests, gspread, openpyxl

# ═══════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════

XLSX_PATH = os.path.join(SCRIPT_DIR, '_data', '#SpeakingMyName Outreach Tracker(1).xlsx')
XLSX_TAB  = 'OrgsCompaniesAssociations'

# ═══════════════════════════════════════════════════
# XLSX READER
# ═══════════════════════════════════════════════════

def read_orgs_from_xlsx():
    """Read orgs from the SMN xlsx tracker. Returns list of dicts."""
    wb = openpyxl.load_workbook(XLSX_PATH)
    ws = wb[XLSX_TAB]

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

        if not association_name:
            continue
        if association_name.lower() in ('n/a', 'none', 'test', ''):
            continue

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
# WEBSITE RESEARCH
# ═══════════════════════════════════════════════════

_MISSION_PATHS = [
    "/about", "/about-us", "/who-we-are", "/our-story", "/mission",
    "/what-we-do", "/our-work", "/programs", "/services", "/initiatives",
    "/impact", "/community", "/diversity", "/dei", "/inclusion",
    "/values", "/our-values", "/who-we-serve",
]


def fetch_url_text(url, timeout=12):
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
        resp = requests.get(url, timeout=timeout, headers=BOT_HEADERS, allow_redirects=True)
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

    # Strategy 0: Extract domain from email
    if email_hint and '@' in email_hint:
        email_domain = email_hint.split('@')[-1].strip().lower()
        skip_email_domains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com',
                              'aol.com', 'icloud.com', 'mail.com', 'protonmail.com',
                              'live.com', 'msn.com', 'ymail.com']
        if email_domain not in skip_email_domains:
            for prefix in ['https://www.', 'https://']:
                url = f'{prefix}{email_domain}'
                try:
                    r = requests.get(url, timeout=(1, 2), headers=BOT_HEADERS, allow_redirects=True)
                    if r.status_code < 400 and len(r.text) > 300:
                        return r.url
                except Exception:
                    pass

    # Strategy 1: Domain pattern guessing
    clean = org_name.lower().strip()
    for suffix in [' inc', ' inc.', ' ltd', ' llc', ', inc', ', inc.', ' the ']:
        clean = clean.replace(suffix, '')
    clean = re.sub(r'[^a-z0-9\s]', '', clean)
    clean = re.sub(r'\s+', '', clean)

    session = requests.Session()
    for url in [
        f"https://www.{clean}.org",
        f"https://{clean}.org",
        f"https://www.{clean}nyc.org",
    ]:
        try:
            r = session.get(url, timeout=(1, 2), headers=BOT_HEADERS, allow_redirects=True)
            if r.status_code < 400 and len(r.text) > 500:
                session.close()
                return r.url
        except Exception:
            pass
    session.close()

    # Strategy 2: Bing search
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(
            "https://www.bing.com/search",
            params={"q": f'"{org_name}" site:.org OR site:.com'},
            headers={**BOT_HEADERS, "Accept-Language": "en-US,en;q=0.9"},
            timeout=10,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
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
    seen, unique = set(), []
    for m in matches:
        prefix = m[:40].lower()
        if prefix not in seen:
            seen.add(prefix)
            unique.append(m)
    return unique[:8]


def research_org(org_name, org_type, email_hint=""):
    """Deep research on an organization. Returns (website_url, research_text, research_notes)."""
    website_url = search_org_website(org_name, org_type, email_hint)

    if not website_url:
        return "", "", f"[No website found for '{org_name}'. Use org name + type ({org_type}) for context.]"

    homepage = fetch_url_text(website_url)
    if homepage["error"]:
        return website_url, "", f"[Website: {website_url} — homepage could not be fetched: {homepage['error']}]"

    all_texts = [f"HOMEPAGE ({homepage.get('title', 'No title')}):\n{homepage['text']}"]
    visited_urls = {website_url.rstrip("/"), homepage["url"].rstrip("/")}

    # Fetch mission-related sub-pages
    base = website_url.rstrip("/")
    for path in _MISSION_PATHS[:10]:
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

    # Search for DEI/inclusion programs
    dei_info = ""
    try:
        import urllib.parse
        from bs4 import BeautifulSoup as BS2
        dei_q = f'"{org_name}" diversity equity inclusion belonging program'
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": dei_q},
            headers=BOT_HEADERS,
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

    combined_text = "\n\n---\n\n".join(all_texts)
    key_info = _extract_key_info(combined_text, org_name)

    research_notes_parts = [f"URL: {website_url}"]
    if homepage.get("title"):
        research_notes_parts.append(f"Title: {homepage['title']}")
    for i, info in enumerate(key_info[:5], 1):
        clean_info = info.strip()[:200]
        if clean_info:
            research_notes_parts.append(f"{i}. {clean_info}")
    if dei_info:
        dei_lines = dei_info.strip().split("\n")
        if dei_lines:
            research_notes_parts.append(f"DEI: {dei_lines[0][:200]}")

    research_notes = "\n".join(research_notes_parts)

    context_parts = []
    if key_info:
        context_parts.append("KEY MISSION/PROGRAM INFO:\n" + "\n".join(f"• {k}" for k in key_info[:6]))
    if dei_info:
        context_parts.append(dei_info)
    full_research_text = "\n\n".join(context_parts)[:4000]

    return website_url, full_research_text, research_notes


# ═══════════════════════════════════════════════════
# EMAIL GENERATION
# ═══════════════════════════════════════════════════

def generate_email_for_org(org, website_url, website_text, research_notes=""):
    """Generate a personalized SMN email for one org."""
    system_prompt = get_prompt()

    followup_label = "FOLLOW-UP: YES" if org['is_followup'] else "FOLLOW-UP: NO"

    prior_context = ""
    if org['is_followup']:
        prior_context = f"""
PRIOR CONTACT CONTEXT:
Our team (Hetal/Hana) reached out to {org['full_name'] or 'the team'} at {org['association_name']} in March 2026.
Current response status: {org['response']}
The email should briefly acknowledge this prior contact and build on it."""

    notes_context = ""
    if org['notes']:
        notes_context = f"""
INTERNAL NOTES (use for personalization if relevant):
{org['notes']}"""

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
        result = call_llm(system_prompt, user_prompt)
        subject = clean_email(result.get('email_subject', ''))
        body = clean_email(result.get('email_body', ''))
        return subject, body
    except Exception as e:
        print(f'  LLM ERROR: {e}')
        return '', ''


# ═══════════════════════════════════════════════════
# SHEET HELPERS
# ═══════════════════════════════════════════════════

def get_existing_rows(ws):
    """Read existing data from the sheet. Returns dict keyed by org name."""
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
            continue

        sheet_row = len(existing) + imported + 2
        row_data = [
            org['full_name'],
            org['title'],
            org['association_name'],
            org['org_type'],
            org['email'],
            'READY',
            org['notes'],
            '',
            '',
            'Yes' if org['is_followup'] else 'No',
            '',
            '',
        ]
        try:
            ws.update(values=[row_data], range_name=f'A{sheet_row}')
            imported += 1
            existing[key] = {'sheet_row': sheet_row, 'status': 'READY', 'subject': '', 'email_body': ''}
        except Exception as e:
            print(f'  ERROR importing {org["association_name"]}: {e}')

    return imported


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════

def main():
    args = parse_args(
        "Generate #SpeakingMyName outreach emails",
        extra_args=[
            {"name": "--import-only", "dest": "import_only", "action": "store_true",
             "help": "Only import from xlsx to sheet"},
            {"name": "--research-only", "dest": "research_only", "action": "store_true",
             "help": "Only research orgs, skip generation"},
        ]
    )

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

    # Connect to sheet
    print('Connecting to Google Sheet...')
    ws = connect_sheet(CAMPAIGN['sheet_tab'], headers=[
        'Full Name', 'Title', 'Association Name', 'Type',
        'Contact Email', 'Status', 'Notes',
        'Email Subject', 'Personalized Email', 'Follow-up?', 'Sent At',
        'Research Notes'
    ])

    # Import orgs into sheet
    print('Importing orgs into sheet...')
    imported = import_orgs_to_sheet(orgs, ws)
    print(f'  {imported} new orgs imported')

    if args.import_only:
        print('Import-only mode — done.')
        return

    # Refresh existing rows after import
    existing = get_existing_rows(ws)

    # Build work list
    work_items = []
    for org in orgs:
        key = org['association_name'].lower().strip()
        sheet_info = existing.get(key, {})
        sheet_row = sheet_info.get('sheet_row', 0)
        current_status = sheet_info.get('status', '')

        if current_status in ('DRAFTED', 'APPROVED', 'SENT') and not args.force and not args.research_only:
            continue
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

    # Phase 1: Research all orgs in parallel
    print('=== PHASE 1: Researching orgs (parallel) ===')
    research_results = {}

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

    # Write research notes to sheet
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
                        ws.update(values=[['RESEARCHED']], range_name=f'F{sheet_row}')
                except Exception:
                    pass

    if args.research_only:
        print(f'Research-only mode — {len(work_items)} orgs researched.')
        print(f'Sheet: https://docs.google.com/spreadsheets/d/{get_sheet_id()}/edit#gid={ws.id}')
        return

    # Phase 2: Generate emails sequentially
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

        print(f'  Generating email...')
        subject, body = generate_email_for_org(org, website_url, website_text, research_notes)

        if not subject or not body:
            print(f'  GENERATION FAILED — skipping')
            continue

        print(f'  Subject: {subject[:80]}')
        print(f'  Body length: {len(body)} chars')

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
        time.sleep(0.5)

    print(f'\nDone! Generated: {generated} emails')
    print(f'Sheet: https://docs.google.com/spreadsheets/d/{get_sheet_id()}/edit#gid={ws.id}')


if __name__ == '__main__':
    main()
