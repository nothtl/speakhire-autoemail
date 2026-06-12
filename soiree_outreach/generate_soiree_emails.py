"""
generate_soiree_emails.py — SpeakHire Soiree 2026 Email Generator

Generates personalized emails for THREE campaign types:
  - sponsor:      Ask companies/orgs to sponsor the Soiree (tiers: $5K–$50K+)
  - individual:   Invite people to attend the Soiree ($150/ticket)
  - hetal_people: Hetal's personal network invitations (from Network of Influence.xlsx)

Reads contacts from CSV (sponsor/individual) or xlsx (hetal_people). Researches
each recipient, generates personalized emails via LLM, writes to Google Sheets.

Usage:
  python generate_soiree_emails.py --csv sponsors.csv --type sponsor
  python generate_soiree_emails.py --csv attendees.csv --type individual
  python generate_soiree_emails.py --type hetal_people
  python generate_soiree_emails.py --type sponsor --rows 5 --preview
"""

# ═══════════════════════════════════════════════════
# IMPORTS
# ═══════════════════════════════════════════════════

import csv, os, re, sys, time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, '..'))
sys.path.insert(0, SCRIPT_DIR)

from shared.config import BOT_HEADERS, LLM_MODEL, BASE_URL
from shared.generator import (
    fix_windows_encoding, parse_args, call_llm,
    clean_email, safe_str, connect_sheet, get_sheet_id,
)
from soiree_prompt import get_prompt, CAMPAIGN, SOIREE_DATE, SOIREE_TIME, SOIREE_VENUE, SOIREE_TICKET

fix_windows_encoding()

import requests, openpyxl

# ═══════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════

PEOPLE_XLSX_PATH = os.path.join(SCRIPT_DIR, '_data', 'Network of Influence.xlsx')
PEOPLE_XLSX_TAB  = 'People'
PEOPLE_START_ROW = 4
PEOPLE_END_ROW   = 129

# ═══════════════════════════════════════════════════
# CSV READER (sponsor / individual)
# ═══════════════════════════════════════════════════

def read_csv(filepath):
    """Read contacts from CSV. Auto-detects column names for sponsor vs individual."""
    contacts = []
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)

        for row in reader:
            name = row.get('name', row.get('full name', row.get('Name', row.get('Full Name',
                   row.get('first name', row.get('First Name', '')))))).strip()
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

            languages = row.get('languages', row.get('Languages', row.get('language', ''))).strip()
            interest_cols = ['career interests', 'career_interests', 'interests', 'Career Interests']
            interests = next((row.get(c, '').strip() for c in interest_cols if row.get(c, '').strip()), '')
            field_cols = ['career field', 'career_field', 'main_career_field', 'Career Field']
            career_field = next((row.get(c, '').strip() for c in field_cols if row.get(c, '').strip()), '')
            notes = row.get('notes', row.get('Notes', row.get('NOTES', ''))).strip()

            if not name and not org:
                continue

            contacts.append({
                'name': name, 'email': email, 'org': org, 'title': title,
                'languages': languages, 'interests': interests,
                'career_field': career_field, 'notes': notes,
            })

    return contacts


# ═══════════════════════════════════════════════════
# XLSX READER (hetal_people)
# ═══════════════════════════════════════════════════

def read_people_from_xlsx():
    """Read contacts from the People tab of Network of Influence.xlsx."""
    wb = openpyxl.load_workbook(PEOPLE_XLSX_PATH)
    ws = wb[PEOPLE_XLSX_TAB]

    people = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=PEOPLE_START_ROW, max_row=PEOPLE_END_ROW, values_only=True), PEOPLE_START_ROW):
        vals = [safe_str(v) for v in row]

        full_name    = vals[2] if len(vals) > 2 else ''
        company      = vals[3] if len(vals) > 3 else ''
        email        = vals[4] if len(vals) > 4 else ''
        title        = vals[5] if len(vals) > 5 else ''
        contact_type = vals[6] if len(vals) > 6 else ''
        website      = vals[8] if len(vals) > 8 else ''
        city         = vals[10] if len(vals) > 10 else ''
        state        = vals[11] if len(vals) > 11 else ''
        linkedin     = vals[14] if len(vals) > 14 else ''
        description  = vals[15] if len(vals) > 15 else ''
        amount       = vals[16] if len(vals) > 16 else ''
        notes        = vals[17] if len(vals) > 17 else ''
        status       = vals[0] if len(vals) > 0 else ''
        tags         = vals[1] if len(vals) > 1 else ''

        if not full_name:
            continue

        # Fix "LastName, FirstName" format
        full_name = full_name.strip()
        if ',' in full_name and not full_name.startswith('http'):
            parts = [p.strip() for p in full_name.split(',', 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                last = parts[0].strip()
                first = re.sub(r'\s*\(.*?\)\s*', '', parts[1]).strip()
                first = re.sub(r'\s*<.*', '', first).strip()
                if first and last:
                    full_name = f"{first} {last}"

        name_parts = full_name.split()
        first_name = name_parts[0] if name_parts else ''
        last_name = name_parts[-1] if len(name_parts) > 1 else ''
        if last_name.lower() in ('jr', 'sr', 'ii', 'iii', 'ed.d'):
            last_name = name_parts[-2] if len(name_parts) > 2 else ''

        if not email or '@' not in email:
            continue

        has_donated = bool(amount and amount != '0' and amount != '0.0')

        people.append({
            'xlsx_row': row_idx, 'full_name': full_name, 'first_name': first_name,
            'last_name': last_name, 'company': company, 'email': email, 'title': title,
            'contact_type': contact_type, 'website': website, 'city': city, 'state': state,
            'linkedin': linkedin, 'description': description, 'amount': amount,
            'notes': notes, 'status': status, 'tags': tags, 'has_donated': has_donated,
            'has_notes': bool(notes),
        })

    return people


# ═══════════════════════════════════════════════════
# RESEARCH — shared scraping utilities
# ═══════════════════════════════════════════════════

def fetch_url_text(url, timeout=10):
    """Fetch a webpage and return structured text. Never crashes."""
    result = {"url": url, "title": "", "text": "", "error": ""}
    if not url:
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
        t = soup.find("title")
        if t:
            result["title"] = t.get_text(strip=True)
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        result["text"] = soup.get_text(separator=" ", strip=True)[:5000]
    except Exception as e:
        result["error"] = str(e)[:200]
    return result


def search_org_website(org_name, email_hint=""):
    """Find an org's website via email domain + domain guessing."""
    if email_hint and '@' in email_hint:
        email_domain = email_hint.split('@')[-1].strip().lower()
        skip = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'aol.com',
                'icloud.com', 'mail.com', 'protonmail.com', 'live.com', 'msn.com']
        if email_domain not in skip:
            for prefix in ['https://www.', 'https://']:
                try:
                    r = requests.get(f'{prefix}{email_domain}', timeout=(1, 2),
                                     headers=BOT_HEADERS, allow_redirects=True)
                    if r.status_code < 400 and len(r.text) > 300:
                        return r.url
                except Exception:
                    pass

    clean = re.sub(r'[^a-z0-9\s]', '', org_name.lower().strip())
    clean = re.sub(r'\s+', '', clean)
    for url in [f"https://www.{clean}.org", f"https://{clean}.org", f"https://www.{clean}.com"]:
        try:
            r = requests.get(url, timeout=(1, 2), headers=BOT_HEADERS, allow_redirects=True)
            if r.status_code < 400 and len(r.text) > 300:
                return r.url
        except Exception:
            pass
    return ""


# ═══════════════════════════════════════════════════
# RESEARCH — sponsor (org website scraping)
# ═══════════════════════════════════════════════════

def research_org(org_name, email_hint=""):
    """Research an org's website. Returns (website_url, research_text, research_notes)."""
    website_url = search_org_website(org_name, email_hint)
    if not website_url:
        return "", "", f"[No website found for '{org_name}']"

    page = fetch_url_text(website_url)
    if page["error"]:
        return website_url, "", f"[Website: {website_url} — could not fetch]"

    text = page["text"]
    keywords = ["mission", "program", "community", "serve", "diversity", "inclusion",
                "equity", "belonging", "impact", "support", "partner", "initiative",
                "csr", "philanthropy", "grant", "workforce", "education", "youth"]
    sentences = [s.strip() for s in text.replace('\n', '. ').split('.') if len(s.strip()) > 30]
    matches = []
    for s in sentences:
        if any(kw in s.lower() for kw in keywords[:6]) and len(s) > 40:
            matches.append(s[:250])

    notes_parts = [f"URL: {website_url}"]
    if page.get("title"):
        notes_parts.append(f"Title: {page['title']}")
    for i, m in enumerate(matches[:5], 1):
        notes_parts.append(f"{i}. {m.strip()}")

    return website_url, "\n".join(f"• {m}" for m in matches[:6])[:4000] if matches else "", "\n".join(notes_parts)


# ═══════════════════════════════════════════════════
# RESEARCH — hetal_people (person profile scraping)
# ═══════════════════════════════════════════════════

def _extract_profile_sentences(text):
    """Extract sentences that describe the person, company, or mission."""
    keywords = [
        "mission", "program", "community", "serve", "education", "youth",
        "student", "school", "leadership", "diversity", "equity", "inclusion",
        "support", "impact", "nonprofit", "philanthropy", "initiative",
        "partner", "donor", "foundation", "grant",
    ]
    sentences = [s.strip() for s in text.replace('\n', '. ').split('.') if len(s.strip()) > 30]
    matches = [s[:250] for s in sentences if any(kw in s.lower() for kw in keywords[:8])]
    seen, unique = set(), []
    for m in matches:
        prefix = m[:40].lower()
        if prefix not in seen:
            seen.add(prefix)
            unique.append(m)
    return unique[:6]


def research_person(person):
    """Research a person using their website + profile. Returns (research_text, research_notes)."""
    research_parts, notes_parts = [], []

    website_url = person.get('website', '')
    if website_url:
        if not website_url.startswith('http'):
            website_url = 'https://' + website_url
        page = fetch_url_text(website_url)
        if not page["error"] and page["text"]:
            notes_parts.append(f"Website: {website_url}")
            if page.get("title"):
                notes_parts.append(f"Title: {page['title']}")
            key_info = _extract_profile_sentences(page["text"])
            for i, info in enumerate(key_info, 1):
                notes_parts.append(f"{i}. {info.strip()[:200]}")
            research_parts.append(f"WEBSITE ({website_url}):\n" + "\n".join(f"• {k}" for k in key_info[:5]))

    company = person.get('company', '')
    title = person.get('title', '')
    if company and title:
        notes_parts.insert(0, f"Role: {title} at {company}")
    elif company:
        notes_parts.insert(0, f"Company: {company}")
    elif title:
        notes_parts.insert(0, f"Title: {title}")

    if person.get('contact_type'):
        notes_parts.append(f"Type: {person['contact_type']}")
    if person.get('has_donated'):
        notes_parts.append(f"Past Donor: ${person['amount']}")
    if person.get('notes'):
        notes_parts.append(f"Notes: {person['notes']}")
        research_parts.append(f"RELATIONSHIP NOTES: {person['notes']}")
    if person.get('description'):
        notes_parts.append(f"Description: {person['description'][:200]}")
        research_parts.append(f"DESCRIPTION: {person['description'][:300]}")
    if person.get('status'):
        notes_parts.append(f"Status: {person['status']}")

    research_text = "\n\n".join(research_parts)[:4000] if research_parts else ""
    research_notes = "\n".join(notes_parts)
    return research_text, research_notes


# ═══════════════════════════════════════════════════
# EMAIL GENERATION
# ═══════════════════════════════════════════════════

def build_user_prompt(contact, campaign_type, research_text, website_url=""):
    """Build the LLM user prompt based on campaign type."""
    name = contact.get('name', '') or contact.get('full_name', '') or 'there'
    org = contact.get('org', '') or contact.get('company', '') or 'your organization'
    title = contact.get('title', '')
    email = contact.get('email', '')
    notes = contact.get('notes', '')

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

The Soiree is on June 24th at Salesforce Tower, NYC. Tiers: $5K–$50K+.
CRITICAL: Reference at least ONE specific program, initiative, or fact about {org}. Tie it to why SpeakHire's mission of launching immigrant/first-gen careers matters to THEM."""

    elif campaign_type == 'individual':
        languages = contact.get('languages', '')
        interests = contact.get('interests', '')
        career_field = contact.get('career_field', '')

        profile = []
        if title: profile.append(f"Job: {title}")
        if org: profile.append(f"Company: {org}")
        if career_field: profile.append(f"Career field: {career_field}")
        if languages: profile.append(f"Languages: {languages}")
        if interests: profile.append(f"Interests: {interests}")
        profile_text = '\n'.join(profile) if profile else '(minimal profile — write a warm, brief invitation)'

        return f"""Write a personalized Soiree INVITATION for:

NAME: {name}
EMAIL: {email or 'N/A'}
{profile_text}
{f'NOTES: {notes}' if notes else ''}

The Soiree is June 24th, 5:30 PM at Salesforce Tower, NYC.
CRITICAL: Make this feel personal to {name}. Use whatever details are available."""

    elif campaign_type == 'hetal_people':
        first = contact.get('first_name', name)
        last = contact.get('last_name', '')
        contact_type = contact.get('contact_type', '')
        person_status = contact.get('status', '')
        has_donated = contact.get('has_donated', False)
        amount = contact.get('amount', '')

        notes_block = ""
        if notes:
            notes_block = f"""
HOW YOU KNOW THEM / RELATIONSHIP NOTES:
{notes}"""

        donor_block = ""
        if has_donated:
            donor_block = f"""
PAST SUPPORTER: This person has donated ${amount} to SpeakHire in the past. Acknowledge this support warmly but don't mention the specific amount."""

        return f"""Write a personal, warm Soiree invitation from Hetal Jani to:

NAME: {name}
FIRST NAME: {first}
LAST NAME: {last}
JOB TITLE: {title or 'Not specified'}
ORGANIZATION: {org or 'Not specified'}
EMAIL: {email}
CONTACT TYPE: {contact_type or 'Not specified'}
STATUS: {person_status or 'Not specified'}
{notes_block}
{donor_block}

RESEARCH ABOUT THIS PERSON:
{research_text if research_text else '(Minimal information available. Write a warm, sincere invitation based on what you know.)'}

The Soiree is on {SOIREE_DATE}, {SOIREE_TIME} at {SOIREE_VENUE}.
Tickets: {SOIREE_TICKET} — do NOT mention price, just direct them to the link.

WHO'S ATTENDING: Harvey Epstein (NYC City Council Member) is speaking. Nneka Nwaifejokwu (G4GC) is speaking. Vicki Teman is being honored. SpeakHire alumni including Isabella Lam, Leyli Hernandez, Naim Bakere, Ousmane Diallo, Cristal Davidson, and Shainu George will share their stories. Hetal Jani and the SpeakHire team will be there.

If any of these people connect to the recipient's world, mention it casually as social proof.

CRITICAL: Find the thread that connects this person's work to SpeakHire's mission. Weave that connection throughout the email — not as a sales pitch, but as a genuine observation."""

    else:
        raise ValueError(f"Unknown type: {campaign_type}")


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════

def main():
    args = parse_args(
        "Generate Soiree outreach emails",
        extra_args=[
            {"name": "--csv", "help": "CSV file of contacts (sponsor/individual)"},
            {"name": "--type", "required": True,
             "choices": ["sponsor", "individual", "hetal_people"],
             "help": "Campaign type"},
        ]
    )

    # --- Read contacts ---
    if args.type == 'hetal_people':
        contacts = read_people_from_xlsx()
        print(f'Read {len(contacts)} people from "{PEOPLE_XLSX_TAB}" tab')
    else:
        if not args.csv:
            print("ERROR: --csv is required for sponsor/individual types")
            sys.exit(1)
        contacts = read_csv(args.csv)
        print(f'Read {len(contacts)} contacts from {args.csv}')

    print(f'Campaign: {args.type}')
    print(f'Using: {LLM_MODEL} via {BASE_URL}')
    print()

    if args.rows:
        contacts = contacts[:args.rows]

    if args.row and args.type == 'hetal_people':
        contacts = [c for c in contacts if c.get('xlsx_row') == args.row]
        if not contacts:
            print(f'No person found at xlsx row {args.row}')
            return

    if args.preview:
        for i, c in enumerate(contacts):
            if args.type == 'hetal_people':
                donor = ' [DONOR]' if c.get('has_donated') else ''
                print(f'{i+1}. {c["full_name"]} ({c["title"]}){donor}')
                print(f'   {c["email"]} | {c["company"]}')
            else:
                print(f'{i+1}. {c["name"] or "?"} | {c["org"] or "?"} | {c["email"] or "?"}')
            if c.get('notes'):
                print(f'   Notes: {c["notes"][:100]}')
        return

    print('Connecting to Google Sheet...')
    ws = connect_sheet(CAMPAIGN['sheet_tab'])
    system_prompt = get_prompt(args.type)

    generated = 0
    for i, contact in enumerate(contacts):
        if args.type == 'hetal_people':
            display_name = contact['full_name']
            org_label = contact.get('title', '?')
        else:
            display_name = contact.get('name') or contact.get('org', '?')
            org_label = contact.get('org', '') or contact.get('name', '?')

        print(f'{i+1}/{len(contacts)}: {display_name}')

        # Research
        website_url = ""
        research_text = ""
        research_notes = ""

        if args.type == 'sponsor' and contact.get('org'):
            print(f'  Researching {contact["org"]}...')
            website_url, research_text, research_notes = research_org(
                contact['org'], contact.get('email', '')
            )
            if website_url:
                print(f'    → {website_url}')

        elif args.type == 'hetal_people':
            print(f'  Researching...')
            research_text, research_notes = research_person(contact)
            if research_notes:
                first_line = research_notes.split('\n')[0][:100]
                print(f'  Found: {first_line}')
            else:
                print(f'  Minimal profile available')

        # Generate
        print(f'  Generating...')
        user_prompt = build_user_prompt(contact, args.type, research_text, website_url)
        try:
            result = call_llm(system_prompt, user_prompt)
            subject = clean_email(result.get('email_subject', ''))
            body = clean_email(result.get('email_draft', ''))
        except Exception as e:
            print(f'  LLM ERROR: {e}')
            continue

        if not body:
            print(f'  EMPTY BODY — skipping')
            continue

        print(f'  Subject: {subject[:80]}')

        # Extra column
        extra = ''
        if args.type == 'individual':
            extras = []
            if contact.get('languages'): extras.append(f"Languages: {contact['languages']}")
            if contact.get('interests'): extras.append(f"Interests: {contact['interests']}")
            if contact.get('career_field'): extras.append(f"Field: {contact['career_field']}")
            extra = '; '.join(extras)
        elif args.type == 'hetal_people':
            if contact.get('has_donated'):
                extra = f"Past donor: ${contact['amount']}"
            if contact.get('notes'):
                if extra: extra += '; '
                extra += f"Notes: {contact['notes'][:100]}"

        # Build row values
        if args.type == 'hetal_people':
            row_values = [
                contact['full_name'], contact.get('title', ''), contact.get('company', ''),
                contact['email'], args.type, 'DRAFTED', contact.get('notes', ''),
                subject, body, research_notes, '', extra,
            ]
        else:
            row_values = [
                contact.get('name', ''), contact.get('title', ''), contact.get('org', ''),
                contact.get('email', ''), args.type, 'DRAFTED', contact.get('notes', ''),
                subject, body, research_notes, '', extra,
            ]

        sheet_row = i + 2
        try:
            ws.update(values=[row_values], range_name=f'A{sheet_row}')
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
